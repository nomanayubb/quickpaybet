from decimal import Decimal
import uuid

from django.db import transaction
from rest_framework.exceptions import ValidationError

from .models import FxRateConfig, Wallet, WalletTransaction


def convert_usd_to_wallet_currency(user, usd_amount: Decimal) -> tuple[Decimal, str, Decimal]:
    """
    Converts a USD amount (what the crypto payment provider always prices in
    - see apps.payments.providers.NOWPaymentsProvider, which has no PKR rail)
    into the given user's own wallet currency, using the live-refreshed rate
    on FxRateConfig (apps.wallet.tasks.refresh_usd_pkr_rate, hourly).

    Returns (wallet_amount, wallet_currency, fx_rate_applied) - the rate is
    always returned even for a USD user (1.0000) so callers can snapshot it
    onto a record uniformly, the same "snapshot, don't reference" discipline
    apps.casino.models.CasinoSession already uses for its own provider-
    currency conversion.
    """
    if user.currency == 'PKR':
        rate = FxRateConfig.get_solo().usd_pkr_rate
        wallet_amount = (usd_amount * rate).quantize(Decimal('0.01'))
        return wallet_amount, 'PKR', rate
    return usd_amount, 'USD', Decimal('1.0000')


def deposit_funds(user, amount: Decimal, description: str = 'Crypto deposit'):
    """
    Immediately adds the deposit amount to the user's wallet.
    This is a simplified mock implementation. In production, the flow will use
    an external crypto provider with webhook verification.
    """
    if amount <= 0:
        raise ValidationError('Amount must be positive.')

    with transaction.atomic():
        wallet, _ = Wallet.objects.select_for_update().get_or_create(user=user)
        wallet.balance += amount
        wallet.save(update_fields=['balance', 'updated_at'])

        txn = WalletTransaction.objects.create(
            wallet=wallet,
            txn_type=WalletTransaction.TxnType.DEPOSIT,
            amount=amount,
            status=WalletTransaction.Status.COMPLETED,
            balance_after=wallet.balance,
            reference_id=f'dep-{uuid.uuid4().hex}',
            description=description,
        )
    return txn


def withdraw_funds(user, amount: Decimal, description: str = 'Crypto withdrawal'):
    """
    Immediately subtracts the withdrawal amount from the user's wallet.
    This is a simplified mock implementation. In production, the flow will
    verify balance, lock the amount, and send the payout through a crypto provider.
    """
    if amount <= 0:
        raise ValidationError('Amount must be positive.')

    with transaction.atomic():
        wallet, _ = Wallet.objects.select_for_update().get_or_create(user=user)
        if wallet.withdrawable_balance < amount:
            raise ValidationError('Insufficient available balance.')

        wallet.balance -= amount
        wallet.save(update_fields=['balance', 'updated_at'])

        txn = WalletTransaction.objects.create(
            wallet=wallet,
            txn_type=WalletTransaction.TxnType.WITHDRAWAL,
            amount=-amount,
            status=WalletTransaction.Status.COMPLETED,
            balance_after=wallet.balance,
            reference_id=f'wd-{uuid.uuid4().hex}',
            description=description,
        )
    return txn
