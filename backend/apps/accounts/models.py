from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _

from .managers import UserManager


class User(AbstractUser):
    class Role(models.TextChoices):
        USER = 'user', _('User')
        AGENT = 'agent', _('Agent')
        MASTER = 'master', _('Master')
        ADMIN = 'admin', _('Admin')

    username = None
    email = models.EmailField(_('email address'), unique=True)
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.USER,
    )
    parent = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='children',
        verbose_name=_('Parent user (agent/master)'),
    )
    commission_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        verbose_name=_('Commission rate (%) on losing bets of direct children'),
    )
    min_bet_amount = models.DecimalField(
        max_digits=20,
        decimal_places=8,
        null=True,
        blank=True,
        verbose_name=_('Minimum bet amount'),
    )
    max_bet_amount = models.DecimalField(
        max_digits=20,
        decimal_places=8,
        null=True,
        blank=True,
        verbose_name=_('Maximum bet amount'),
    )
    is_betting_enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        return self.email


class PasswordResetToken(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='password_reset_tokens'
    )
    token = models.CharField(max_length=100, unique=True)
    is_used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Password Reset Token'
        verbose_name_plural = 'Password Reset Tokens'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user.email} – {self.token[:8]}...'
