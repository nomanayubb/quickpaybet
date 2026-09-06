from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from apps.bets.models import Bet
from apps.sports.models import Match


class ExchangeConfig(models.Model):
    """
    Singleton settings row for the exchange (like ExchangeConfig.get_solo()).
    Admin-editable via Django admin - no redeploy needed to change the rate.
    """
    commission_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('5.00'),
        validators=[MinValueValidator(Decimal('0'))],
        verbose_name='Commission rate (%) on exchange winnings',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Exchange Config'
        verbose_name_plural = 'Exchange Config'

    def __str__(self):
        return f'Exchange commission: {self.commission_rate}%'

    @classmethod
    def get_solo(cls) -> 'ExchangeConfig':
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class ExchangeOrder(models.Model):
    class Side(models.TextChoices):
        BACK = 'back', 'Back'
        LAY = 'lay', 'Lay'

    class Status(models.TextChoices):
        OPEN = 'open', 'Open'
        CANCELLED = 'cancelled', 'Cancelled'
        SETTLED = 'settled', 'Settled'
        VOID = 'void', 'Void'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='exchange_orders',
    )
    match = models.ForeignKey(
        Match,
        on_delete=models.PROTECT,
        related_name='exchange_orders',
    )
    selection = models.CharField(max_length=10, choices=Bet.Selection.choices)
    side = models.CharField(max_length=4, choices=Side.choices)
    odds = models.DecimalField(max_digits=10, decimal_places=2)
    stake = models.DecimalField(max_digits=20, decimal_places=8)
    matched_stake = models.DecimalField(max_digits=20, decimal_places=8, default=Decimal('0'))
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Exchange Order'
        verbose_name_plural = 'Exchange Orders'

    def __str__(self):
        return f'{self.user.email} – {self.side} {self.selection} @ {self.odds} ({self.match})'

    @property
    def unmatched_stake(self) -> Decimal:
        return self.stake - self.matched_stake

    @property
    def liability(self) -> Decimal:
        """The amount at risk (exposure) for this order's full requested stake."""
        if self.side == self.Side.LAY:
            return self.stake * (self.odds - 1)
        return self.stake


class ExchangeFill(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        BACK_WON = 'back_won', 'Back Won'
        LAY_WON = 'lay_won', 'Lay Won'
        VOID = 'void', 'Void'

    match = models.ForeignKey(
        Match,
        on_delete=models.PROTECT,
        related_name='exchange_fills',
    )
    selection = models.CharField(max_length=10, choices=Bet.Selection.choices)
    back_order = models.ForeignKey(
        ExchangeOrder,
        on_delete=models.PROTECT,
        related_name='back_fills',
    )
    lay_order = models.ForeignKey(
        ExchangeOrder,
        on_delete=models.PROTECT,
        related_name='lay_fills',
    )
    odds = models.DecimalField(max_digits=10, decimal_places=2)
    stake = models.DecimalField(max_digits=20, decimal_places=8)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    commission_amount = models.DecimalField(
        max_digits=20, decimal_places=8, null=True, blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Exchange Fill'
        verbose_name_plural = 'Exchange Fills'

    def __str__(self):
        return f'Fill #{self.pk} – {self.selection} @ {self.odds} – {self.stake}'
