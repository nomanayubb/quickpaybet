from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class CashbackConfig(models.Model):
    """
    Singleton settings row for the loss-cashback program (same pattern as
    apps.exchange.models.ExchangeConfig) - admin-editable via Django admin,
    no redeploy needed to change it.
    """
    is_enabled = models.BooleanField(
        default=False,
        verbose_name='Cashback enabled (global default)',
    )
    default_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('0'),
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('99'))],
        verbose_name='Default cashback rate (%) on net losses',
    )
    default_wagering_multiplier = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal('0'),
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('20'))],
        verbose_name='Default wagering multiplier (0 = immediately withdrawable)',
    )
    deduct_original_on_unlock = models.BooleanField(
        default=False,
        verbose_name='Deduct original cashback amount when the wagering requirement clears',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Cashback Config'
        verbose_name_plural = 'Cashback Config'

    def __str__(self):
        return f'Cashback: {"on" if self.is_enabled else "off"}, {self.default_rate}%, {self.default_wagering_multiplier}x'

    @classmethod
    def get_solo(cls) -> 'CashbackConfig':
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class CashbackCredit(models.Model):
    """
    One row per cashback credit event, triggered by a single losing bet,
    parlay, or exchange fill. Every rule that determined this credit's
    amount and requirement is snapshotted here at creation time (rate,
    multiplier, deduct_original_on_unlock) so a later admin settings change
    can never retroactively alter an already-issued credit or corrupt a
    user's in-flight wagering progress - see rule 21 in the master doc.
    """
    class Status(models.TextChoices):
        LOCKED = 'locked', 'Locked'
        UNLOCKED = 'unlocked', 'Unlocked'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='cashback_credits',
    )
    source_description = models.CharField(max_length=255)
    loss_amount = models.DecimalField(max_digits=20, decimal_places=8)
    rate_applied = models.DecimalField(max_digits=5, decimal_places=2)
    cashback_amount = models.DecimalField(max_digits=20, decimal_places=8)
    multiplier_applied = models.DecimalField(max_digits=6, decimal_places=2)
    wagering_required = models.DecimalField(max_digits=20, decimal_places=8)
    wagering_progress = models.DecimalField(max_digits=20, decimal_places=8, default=Decimal('0'))
    deduct_original_on_unlock = models.BooleanField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.LOCKED)
    created_at = models.DateTimeField(auto_now_add=True)
    unlocked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Cashback Credit'
        verbose_name_plural = 'Cashback Credits'

    def __str__(self):
        return f'{self.user.email} – {self.cashback_amount} ({self.status})'

    @property
    def wagering_remaining(self) -> Decimal:
        return max(self.wagering_required - self.wagering_progress, Decimal('0'))
