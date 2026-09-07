from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class RewardPackage(models.Model):
    """
    Admin-managed catalog of deposit-milestone rewards: a user whose
    lifetime confirmed deposits cross `deposit_threshold` gets
    `commission_rate_bonus` added permanently to their User.commission_rate
    (see apps.rewards.services.apply_reward_packages()). Soft-disable via
    `is_active` - there is no hard-delete anywhere in this project and this
    isn't the first (see Sport.is_active).
    """
    name = models.CharField(max_length=100)
    deposit_threshold = models.DecimalField(
        max_digits=20,
        decimal_places=8,
        validators=[MinValueValidator(Decimal('0.00000001'))],
        verbose_name='Cumulative lifetime deposit required',
    )
    commission_rate_bonus = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('99'))],
        verbose_name='Commission rate (%) bonus added permanently when earned',
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['deposit_threshold']
        verbose_name = 'Reward Package'
        verbose_name_plural = 'Reward Packages'

    def __str__(self):
        return f'{self.name} (deposit {self.deposit_threshold} -> +{self.commission_rate_bonus}%)'


class UserRewardClaim(models.Model):
    """
    Records that `user` has earned `package` - one row per user per package,
    ever. `commission_rate_bonus_applied` snapshots the package's bonus at
    the moment it was claimed, so editing a package's bonus later can never
    retroactively change what a user already has (same pattern as
    ExchangeFill.commission_amount / CashbackCredit - see master doc rule 21).
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='reward_claims',
    )
    package = models.ForeignKey(
        RewardPackage,
        on_delete=models.PROTECT,
        related_name='claims',
    )
    commission_rate_bonus_applied = models.DecimalField(max_digits=5, decimal_places=2)
    deposit_total_at_claim = models.DecimalField(max_digits=20, decimal_places=8)
    claimed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-claimed_at']
        unique_together = ('user', 'package')
        verbose_name = 'User Reward Claim'
        verbose_name_plural = 'User Reward Claims'

    def __str__(self):
        return f'{self.user.email} earned {self.package.name}'
