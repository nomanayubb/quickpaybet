import os
import uuid
from decimal import Decimal

from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.wallet.services import deposit_funds, withdraw_funds

from .models import CryptoPayment
from .providers import get_payment_provider


def create_deposit(user, amount: Decimal, currency: str = 'USDT', provider: str | None = None) -> CryptoPayment:
    """
    Asks the configured payment provider for a deposit address,
    stores a PENDING CryptoPayment, and returns that record.
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

    return CryptoPayment.objects.create(
        user=user,
        amount=amount,
        currency=currency,
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
    """
    if amount <= 0:
        raise ValidationError('Amount must be positive.')
    if not address:
        raise ValidationError('Withdrawal address is required.')

    external_id = f"wd_{uuid.uuid4().hex}"

    with transaction.atomic():
        txn = withdraw_funds(
            user=user,
            amount=amount,
            description=f'Crypto withdrawal to {address} via {currency}',
        )

        payment = CryptoPayment.objects.create(
            user=user,
            amount=amount,
            currency=currency,
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
                amount=amount,
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
        deposit_funds(
            user=payment.user,
            amount=payment.amount,
            description=f"Crypto deposit confirmed ({payment.external_id})"
        )
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
