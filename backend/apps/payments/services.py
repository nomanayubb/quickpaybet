import os
import uuid
from decimal import Decimal

from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.rewards.services import apply_reward_packages
from apps.wallet.services import convert_usd_to_wallet_currency, deposit_funds, withdraw_funds

from .models import CryptoPayment
from .providers import get_payment_provider


def create_deposit(user, amount: Decimal, currency: str = 'USDT', provider: str | None = None) -> CryptoPayment:
    """
    Asks the configured payment provider for a deposit address,
    stores a PENDING CryptoPayment, and returns that record.

    `amount` is always USD - NOWPayments has no PKR rail, so the crypto
    invoice underneath is always priced in USD regardless of the user's own
    wallet currency (see apps.payments.providers.NOWPaymentsProvider). What
    actually lands in the wallet on success is `wallet_amount` in
    `wallet_currency` (see convert_usd_to_wallet_currency) - for a PKR user
    this is the live-converted PKR figure; for a USD user it's identical to
    `amount`.
    """
    if amount <= 0:
        raise ValidationError('Amount must be positive.')

    if provider is None:
        provider = os.getenv('PAYMENT_PROVIDER', 'mock').lower()

    provider_obj = get_payment_provider_wrapper(provider)

    provider_result = provider_obj.create_deposit(
        user=user,
        amount=amount,
        currency=currency,
    )

    external_id = provider_result.get('external_id', '')
    address = provider_result.get('address', '')

    wallet_amount, wallet_currency, fx_rate_applied = convert_usd_to_wallet_currency(user, amount)

    return CryptoPayment.objects.create(
        user=user,
        amount=amount,
        currency=currency,
        wallet_amount=wallet_amount,
        wallet_currency=wallet_currency,
        fx_rate_applied=fx_rate_applied,
        payment_type=CryptoPayment.PaymentType.DEPOSIT,
        status=CryptoPayment.Status.PENDING,
        provider=provider_result.get('provider', provider_obj.name),
        external_id=external_id,
        address=address,
    )


def create_withdrawal_request(user, amount: Decimal, address: str, currency: str = 'USDT') -> CryptoPayment:
    """
    The real withdrawal flow: deduct the stake from the wallet ledger (atomic,
    row-locked, same as any other wallet mutation), then hand the payout off
    to the configured payment provider, and record a CryptoPayment row so
    there is always a real record an admin/provider can act on.

    If the provider call fails, the wallet debit is reversed (refunded) and
    the payment is marked failed — the user's balance must never be reduced
    for a withdrawal that was never actually handed to a payment provider.

    `amount` is always USD/crypto-quantity - it's what actually gets sent to
    NOWPayments' payout API (no PKR rail there either). The wallet itself is
    debited `wallet_amount` in the user's own `wallet_currency` (converted
    via convert_usd_to_wallet_currency) so a PKR user's balance moves by the
    right PKR figure even though the payout underneath is still USD/crypto.
    """
    if amount <= 0:
        raise ValidationError('Amount must be positive.')
    if not address:
        raise ValidationError('Withdrawal address is required.')

    external_id = f"wd_{uuid.uuid4().hex}"
    wallet_amount, wallet_currency, fx_rate_applied = convert_usd_to_wallet_currency(user, amount)

    with transaction.atomic():
        txn = withdraw_funds(
            user=user,
            amount=wallet_amount,
            description=f'Crypto withdrawal to {address} via {currency}',
        )

        payment = CryptoPayment.objects.create(
            user=user,
            amount=amount,
            currency=currency,
            wallet_amount=wallet_amount,
            wallet_currency=wallet_currency,
            fx_rate_applied=fx_rate_applied,
            payment_type=CryptoPayment.PaymentType.WITHDRAWAL,
            status=CryptoPayment.Status.PENDING,
            provider=os.getenv('PAYMENT_PROVIDER', 'mock').lower(),
            external_id=external_id,
            address=address,
        )

    provider_obj = get_payment_provider()
    try:
        provider_result = provider_obj.create_withdrawal(
            user=user,
            amount=amount,
            address=address,
            currency=currency,
            external_id=external_id,
        )
    except Exception as exc:
        # The provider could not be reached / rejected the payout.
        # The user must get their money back — never leave a debited
        # balance with no real payout in flight.
        with transaction.atomic():
            deposit_funds(
                user=user,
                amount=wallet_amount,
                description=f'Withdrawal refund – provider error ({payment.external_id})',
            )
            payment.status = CryptoPayment.Status.FAILED
            payment.save(update_fields=['status', 'updated_at'])
        raise ValidationError(f'Withdrawal could not be processed: {exc}')

    provider_status = str(provider_result.get('status', 'pending')).lower()
    payment.provider = provider_result.get('provider', provider_obj.name)
    if provider_status in ('success', 'completed', 'finished'):
        payment.status = CryptoPayment.Status.COMPLETED
    elif provider_status in ('failed', 'rejected'):
        payment.status = CryptoPayment.Status.FAILED
    # Otherwise it stays PENDING – most real payout APIs require manual/2FA
    # approval on the provider side before funds actually move.
    payment.save(update_fields=['provider', 'status', 'updated_at'])

    return payment


def initiate_manual_payout(payment_id: int, email: str, password: str) -> dict:
    """
    Admin-initiated payout, step 1 of 2 (see also confirm_manual_payout).

    NOWPayments requires human-in-the-loop 2FA for every payout batch - this
    cannot be fully automated away (nor should it be; that's their security
    control, not a gap in this codebase - see docs/goal.txt discussion).
    This function logs into NOWPayments with the admin-supplied email and
    password (used only in-memory for this call - never written to the
    database, a file, or a log line) to get a short-lived JWT, then asks
    NOWPayments to create the payout batch for this specific withdrawal.

    Returns the JWT and batch id so the view can hold them (session-only,
    never persisted) just long enough for the admin to enter the 2FA code
    in confirm_manual_payout - NOWPayments' JWTs expire in ~5 minutes.
    """
    from .providers import NOWPaymentsProvider

    try:
        payment = CryptoPayment.objects.get(
            pk=payment_id,
            payment_type=CryptoPayment.PaymentType.WITHDRAWAL,
            status=CryptoPayment.Status.PENDING,
        )
    except CryptoPayment.DoesNotExist:
        raise ValidationError('Pending withdrawal not found.')

    provider = get_payment_provider()
    if not isinstance(provider, NOWPaymentsProvider):
        raise ValidationError(
            'Manual payout processing requires PAYMENT_PROVIDER=nowpayments.'
        )

    try:
        jwt_token = provider.authenticate(email, password)
    except Exception as exc:
        raise ValidationError(f'NOWPayments login failed: {exc}')

    try:
        result = provider.create_withdrawal(
            user=payment.user,
            amount=payment.amount,
            address=payment.address,
            currency=payment.currency,
            external_id=payment.external_id,
            jwt_token=jwt_token,
        )
    except Exception as exc:
        raise ValidationError(f'Could not create the payout batch: {exc}')

    batch_id = str(result.get('batch_withdrawal_id') or result.get('external_id') or '')
    if not batch_id:
        raise ValidationError('NOWPayments did not return a batch id for this payout.')

    payment.provider = result.get('provider', provider.name)
    payment.external_id = batch_id
    payment.save(update_fields=['provider', 'external_id', 'updated_at'])

    return {'batch_id': batch_id, 'jwt_token': jwt_token, 'payment_id': payment.id}


def confirm_manual_payout(payment_id: int, batch_id: str, verification_code: str, jwt_token: str) -> CryptoPayment:
    """
    Admin-initiated payout, step 2 of 2: confirm the batch with the 2FA code
    NOWPayments requires. The JWT here is the one obtained in
    initiate_manual_payout moments earlier - it is never stored anywhere
    beyond the admin's own session between these two steps.
    """
    from .providers import NOWPaymentsProvider

    try:
        payment = CryptoPayment.objects.get(
            pk=payment_id,
            payment_type=CryptoPayment.PaymentType.WITHDRAWAL,
        )
    except CryptoPayment.DoesNotExist:
        raise ValidationError('Withdrawal not found.')

    provider = get_payment_provider()
    if not isinstance(provider, NOWPaymentsProvider):
        raise ValidationError(
            'Manual payout processing requires PAYMENT_PROVIDER=nowpayments.'
        )

    try:
        provider.verify_payout(batch_id, verification_code, jwt_token)
    except Exception as exc:
        # A non-2xx response from NOWPayments means the code was wrong, the
        # JWT expired (they last ~5 minutes), or the batch was rejected.
        # Leave the payment PENDING - the admin can retry with a fresh code,
        # or NOWPayments' own dashboard is always the fallback to check on
        # or complete this payout manually.
        raise ValidationError(f'Payout verification failed: {exc}')

    payment.status = CryptoPayment.Status.COMPLETED
    payment.save(update_fields=['status', 'updated_at'])
    return payment


def handle_deposit_success(external_id: str) -> CryptoPayment:
    """
    Called by the payment webhook when a deposit is confirmed.
    Credits the user's wallet and marks the payment completed.
    """
    try:
        payment = CryptoPayment.objects.get(
            external_id=external_id,
            payment_type=CryptoPayment.PaymentType.DEPOSIT,
            status=CryptoPayment.Status.PENDING,
        )
    except CryptoPayment.DoesNotExist:
        raise ValidationError('Pending payment not found.')

    with transaction.atomic():
        txn = deposit_funds(
            user=payment.user,
            amount=payment.wallet_amount if payment.wallet_amount is not None else payment.amount,
            description=f"Crypto deposit confirmed ({payment.external_id})"
        )
        apply_reward_packages(payment.user, txn.wallet)
        payment.status = CryptoPayment.Status.COMPLETED
        payment.save(update_fields=['status', 'updated_at'])

    return payment


def get_payment_provider_wrapper(provider_name: str = ''):
    """
    Convenience wrapper to keep imports clean in this file.
    """
    if not provider_name:
        provider_name = os.getenv('PAYMENT_PROVIDER', 'mock').lower()
    provider_map = {}

    # Import here to avoid circular dependency at module load time.
    from .providers import MockPaymentProvider, NOWPaymentsProvider

    provider_map['mock'] = MockPaymentProvider
    provider_map['nowpayments'] = NOWPaymentsProvider

    try:
        provider_class = provider_map[provider_name]
    except KeyError:
        raise NotImplementedError(f'Unknown payment provider: {provider_name}')

    return provider_class()
