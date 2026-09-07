from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Sport(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    provider_key = models.CharField(max_length=100, blank=True, default='')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Sport'
        verbose_name_plural = 'Sports'

    def __str__(self):
        return self.name


class Tournament(models.Model):
    sport = models.ForeignKey(
        Sport,
        on_delete=models.CASCADE,
        related_name='tournaments'
    )
    name = models.CharField(max_length=200)
    season = models.CharField(max_length=50, blank=True, default='')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = ('sport', 'name', 'season')
        verbose_name = 'Tournament'
        verbose_name_plural = 'Tournaments'

    def __str__(self):
        return f'{self.sport.name} – {self.name}'


class Match(models.Model):
    class Status(models.TextChoices):
        SCHEDULED = 'scheduled', 'Scheduled'
        LIVE = 'live', 'Live'
        FINISHED = 'finished', 'Finished'
        CANCELLED = 'cancelled', 'Cancelled'

    sport = models.ForeignKey(
        Sport,
        on_delete=models.PROTECT,
        related_name='matches'
    )
    tournament = models.ForeignKey(
        Tournament,
        on_delete=models.SET_NULL,
        related_name='matches',
        null=True,
        blank=True
    )
    home_team = models.CharField(max_length=150)
    away_team = models.CharField(max_length=150)
    start_time = models.DateTimeField()
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.SCHEDULED
    )
    home_score = models.PositiveSmallIntegerField(null=True, blank=True)
    away_score = models.PositiveSmallIntegerField(null=True, blank=True)
    odds_home = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True
    )
    odds_draw = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True
    )
    odds_away = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['start_time']
        verbose_name = 'Match'
        verbose_name_plural = 'Matches'

    def __str__(self):
        return f'{self.home_team} v {self.away_team}'


class RealtimeOddsConfig(models.Model):
    """
    Singleton settings row for on-demand real-time odds refresh (same
    get_solo()/pk=1 pattern as apps.exchange.models.ExchangeConfig and
    apps.cashback.models.CashbackConfig). Every timing field is bounded
    with validators so an admin can adjust these freely from Django admin
    without ever being able to enter a value that breaks the site.

    See apps/sports/realtime.py for how these are actually used - there is
    no separate background timer anywhere; a viewer's own poll request is
    what triggers a refresh check, at most once per configured interval
    per sport, regardless of how many viewers are polling at once.
    """
    is_enabled = models.BooleanField(
        default=False,
        verbose_name='Real-time odds refresh enabled (global switch)',
    )
    live_refresh_seconds = models.PositiveIntegerField(
        default=20,
        validators=[MinValueValidator(5), MaxValueValidator(120)],
        verbose_name='Refresh interval (seconds) for live/in-play matches',
    )
    not_started_refresh_seconds = models.PositiveIntegerField(
        default=120,
        validators=[MinValueValidator(30), MaxValueValidator(3600)],
        verbose_name='Refresh interval (seconds) for matches that have not started yet',
    )
    proximity_boost_seconds = models.PositiveIntegerField(
        default=60,
        validators=[MinValueValidator(10), MaxValueValidator(600)],
        verbose_name='Refresh interval (seconds) once a match is close to starting',
    )
    proximity_window_minutes = models.PositiveIntegerField(
        default=30,
        validators=[MinValueValidator(5), MaxValueValidator(180)],
        verbose_name='How close to start time (minutes) counts as "close enough" for the boosted interval',
    )
    min_viewers_for_realtime = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(1000)],
        verbose_name='Minimum concurrent viewers on a match before its odds refresh in real time',
    )
    bet_acceptance_delay_seconds = models.PositiveIntegerField(
        default=5,
        validators=[MinValueValidator(0), MaxValueValidator(30)],
        verbose_name='Odds re-validation window (seconds) at bet placement',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Realtime Odds Config'
        verbose_name_plural = 'Realtime Odds Config'

    def __str__(self):
        return f'Realtime odds: {"on" if self.is_enabled else "off"}, live={self.live_refresh_seconds}s'

    @classmethod
    def get_solo(cls) -> 'RealtimeOddsConfig':
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
