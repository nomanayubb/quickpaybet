from django.conf import settings
from django.db import models


class CryptoPayment(models.Model):
    class PaymentType(models.TextChoices):
        DEPOSIT = 'deposit', 'Deposit'
        WITHDRAWAL = 'withdrawal', 'Withdrawal'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='crypto_payments'
    )
    amount = models.DecimalField(max_digits=20, decimal_places=8)
    currency = models.CharField(max_length=20, default='USDT')
    wallet_amount = models.DecimalField(
        max_digits=20, decimal_places=8, null=True, blank=True,
        verbose_name='Amount actually credited/debited on the wallet, in wallet_currency '
                     '(equals `amount` for a USD wallet; converted via fx_rate_applied for a PKR wallet)',
    )
    wallet_currency = models.CharField(max_length=3, default='USD')
    fx_rate_applied = models.DecimalField(
        max_digits=10, decimal_places=4, default=1,
        verbose_name='USD -> wallet_currency rate snapshotted at request time (1.0000 for a USD wallet)',
    )
    payment_type = models.CharField(
        max_length=20,
        choices=PaymentType.choices,
        default=PaymentType.DEPOSIT
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING
    )
    provider = models.CharField(max_length=100, default='mock')
    external_id = models.CharField(max_length=255, unique=True)
    address = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Crypto Payment'
        verbose_name_plural = 'Crypto Payments'

    def __str__(self):
        return f'{self.user.email} – {self.payment_type} – {self.amount} {self.currency}'
