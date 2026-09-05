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
