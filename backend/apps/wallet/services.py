from decimal import Decimal
import uuid

from django.db import transaction
from rest_framework.exceptions import ValidationError

from .models import Wallet, WalletTransaction


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
        if wallet.balance < amount:
            raise ValidationError('Insufficient balance.')

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
