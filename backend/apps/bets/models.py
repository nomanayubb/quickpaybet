from django.conf import settings
from django.db import models

from apps.sports.models import Match


class Bet(models.Model):
    class Selection(models.TextChoices):
        HOME = 'home', 'Home'
        DRAW = 'draw', 'Draw'
        AWAY = 'away', 'Away'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        WON = 'won', 'Won'
        LOST = 'lost', 'Lost'
        REFUNDED = 'refunded', 'Refunded'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='bets',
    )
    match = models.ForeignKey(
        Match,
        on_delete=models.PROTECT,
        related_name='bets',
    )
    selection = models.CharField(max_length=10, choices=Selection.choices)
    odds = models.DecimalField(max_digits=10, decimal_places=2)
    stake = models.DecimalField(max_digits=20, decimal_places=8)
    potential_payout = models.DecimalField(max_digits=20, decimal_places=8)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Bet'
        verbose_name_plural = 'Bets'

    def __str__(self):
        return f'{self.user.email} – {self.match.home_team} v {self.match.away_team} ({self.selection})'
