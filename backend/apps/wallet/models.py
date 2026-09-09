from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class FxRateConfig(models.Model):
    """
    Singleton (same get_solo()/pk=1 pattern as apps.casino.models.CasinoConfig)
    holding the single USD->PKR rate every PKR-currency user's deposits,
    withdrawals, and balance conversions are computed against.

    `usd_pkr_rate` is the number actually used everywhere - normally kept
    fresh by apps.wallet.tasks.refresh_usd_pkr_rate (Celery beat, hourly,
    see config/celery.py) pulling a live rate from an external FX API. If
    that live fetch ever fails (API down, key missing, network error) the
    task leaves this field untouched at its last known-good value rather
    than zeroing it out or blocking deposits - `last_sync_error` records
    what went wrong for an admin to see, but never blocks money movement.

    `is_manual_override`, when turned on by an admin (Django admin site),
    makes the refresh task skip the live fetch entirely and leaves this
    rate exactly as the admin set it, indefinitely - an escape hatch if the
    live source ever needs to be bypassed.
    """
    usd_pkr_rate = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        default=Decimal('280.0000'),
        validators=[MinValueValidator(Decimal('1.0000')), MaxValueValidator(Decimal('10000.0000'))],
        verbose_name='USD -> PKR rate (used for every PKR-currency user\'s deposits/withdrawals/balance)',
    )
    is_manual_override = models.BooleanField(
        default=False,
        verbose_name='Manual override (when on, the hourly live-rate refresh is skipped and this rate is used as-is)',
    )
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_sync_error = models.CharField(max_length=500, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'FX Rate Config'
        verbose_name_plural = 'FX Rate Config'

    def __str__(self):
        return f'1 USD = {self.usd_pkr_rate} PKR ({"manual" if self.is_manual_override else "live"})'

    @classmethod
    def get_solo(cls) -> 'FxRateConfig':
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Wallet(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='wallet'
    )
    balance = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    reserved_balance = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    locked_cashback_balance = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Wallet'
        verbose_name_plural = 'Wallets'

    def __str__(self):
        return f'{self.user.email} – {self.balance}'

    @property
    def available_balance(self):
        return self.balance - self.reserved_balance

    @property
    def withdrawable_balance(self):
        """
        What can actually leave the platform right now: available_balance
        (already excluding exchange exposure) further reduced by any
        cashback still locked behind a wagering requirement. Locked
        cashback money stays fully usable for placing bets/orders - only
        available_balance (not this) gates that - it just can't be
        withdrawn until apps.cashback.services.record_wager() unlocks it.
        """
        return self.available_balance - self.locked_cashback_balance


class WalletTransaction(models.Model):
    class TxnType(models.TextChoices):
        DEPOSIT = 'deposit', 'Deposit'
        WITHDRAWAL = 'withdrawal', 'Withdrawal'
        BET_PLACED = 'bet_placed', 'Bet Placed'
        BET_WON = 'bet_won', 'Bet Won'
        BET_REFUND = 'bet_refund', 'Bet Refund'
        COMMISSION = 'commission', 'Commission'
        ADJUSTMENT = 'adjustment', 'Adjustment'
        EXCHANGE_WON = 'exchange_won', 'Exchange Won'
        EXCHANGE_LOST = 'exchange_lost', 'Exchange Lost'
        CASHBACK_CREDIT = 'cashback_credit', 'Cashback Credit'
        CASHBACK_CLAWBACK = 'cashback_clawback', 'Cashback Clawback'
        CASINO_SESSION_OPEN = 'casino_session_open', 'Casino Session Open'
        CASINO_WIN = 'casino_win', 'Casino Win'
        CASINO_BET = 'casino_bet', 'Casino Bet'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'
        CANCELLED = 'cancelled', 'Cancelled'

    wallet = models.ForeignKey(
        Wallet,
        on_delete=models.PROTECT,
        related_name='transactions'
    )
    txn_type = models.CharField(max_length=20, choices=TxnType.choices)
    amount = models.DecimalField(max_digits=20, decimal_places=8)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING
    )
    balance_after = models.DecimalField(
        max_digits=20,
        decimal_places=8,
        null=True,
        blank=True
    )
    reference_id = models.CharField(max_length=255, blank=True, default='')
    description = models.CharField(max_length=500, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Wallet Transaction'
        verbose_name_plural = 'Wallet Transactions'

    def __str__(self):
        return f'{self.wallet_id} – {self.txn_type} – {self.amount}'
