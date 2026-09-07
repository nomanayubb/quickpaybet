from django.conf import settings
from django.db import models


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
