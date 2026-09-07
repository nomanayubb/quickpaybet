from decimal import Decimal

from django.db.models import Sum

from apps.accounts.models import User
from apps.wallet.models import Wallet, WalletTransaction

from .models import RewardPackage, UserRewardClaim


def get_lifetime_deposit_total(wallet: Wallet) -> Decimal:
    return WalletTransaction.objects.filter(
        wallet=wallet,
        txn_type=WalletTransaction.TxnType.DEPOSIT,
        status=WalletTransaction.Status.COMPLETED,
    ).aggregate(s=Sum('amount'))['s'] or Decimal('0')


def apply_reward_packages(user, wallet: Wallet) -> list:
    """
    Must be called from inside an existing atomic() block, right after a
    real confirmed deposit (apps.payments.services.handle_deposit_success)
    - that function's own PENDING-status guard is what makes this safe to
    call exactly once per real deposit; no separate idempotency mechanism
    is needed here beyond UserRewardClaim's own unique_together, which
    additionally guarantees a package can never be double-claimed even
    under a race.

    A single large deposit can cross multiple package tiers at once - all
    newly-qualifying packages are applied, not just the highest one.
    Returns the list of newly-created UserRewardClaim rows (empty if none
    newly qualified - the common case for most deposits).
    """
    locked_user = User.objects.select_for_update().get(pk=user.pk)
    lifetime_total = get_lifetime_deposit_total(wallet)

    already_claimed_ids = UserRewardClaim.objects.filter(user=locked_user).values_list('package_id', flat=True)
    qualifying_packages = RewardPackage.objects.filter(
        is_active=True,
        deposit_threshold__lte=lifetime_total,
    ).exclude(pk__in=already_claimed_ids)

    new_claims = []
    for package in qualifying_packages:
        locked_user.commission_rate += package.commission_rate_bonus
        new_claims.append(
            UserRewardClaim(
                user=locked_user,
                package=package,
                commission_rate_bonus_applied=package.commission_rate_bonus,
                deposit_total_at_claim=lifetime_total,
            )
        )

    if new_claims:
        locked_user.save(update_fields=['commission_rate'])
        UserRewardClaim.objects.bulk_create(new_claims)

    return new_claims
