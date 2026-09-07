from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from apps.bets.services import place_bet, settle_bet
from apps.sports.models import Match, Sport
from apps.wallet.models import Wallet
from apps.wallet.services import deposit_funds

from .models import RewardPackage, UserRewardClaim
from .services import apply_reward_packages, get_lifetime_deposit_total

User = get_user_model()


class RewardPackageServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='rewarduser@example.com', password='testpass123')
        self.wallet = Wallet.objects.get(user=self.user)

    def _deposit(self, amount):
        with transaction.atomic():
            txn = deposit_funds(self.user, Decimal(amount))
            apply_reward_packages(self.user, txn.wallet)
        self.user.refresh_from_db()
        return txn

    def test_deposit_below_threshold_does_not_trigger(self):
        RewardPackage.objects.create(name='Bronze', deposit_threshold=Decimal('1000'), commission_rate_bonus=Decimal('2.00'))
        self._deposit('999')
        self.assertEqual(self.user.commission_rate, Decimal('0'))
        self.assertEqual(UserRewardClaim.objects.count(), 0)

    def test_deposit_crossing_threshold_exactly_triggers(self):
        RewardPackage.objects.create(name='Bronze', deposit_threshold=Decimal('1000'), commission_rate_bonus=Decimal('2.00'))
        self._deposit('1000')
        self.assertEqual(self.user.commission_rate, Decimal('2.00'))
        claim = UserRewardClaim.objects.get()
        self.assertEqual(claim.commission_rate_bonus_applied, Decimal('2.00'))
        self.assertEqual(claim.deposit_total_at_claim, Decimal('1000'))

    def test_threshold_crossed_across_multiple_deposits(self):
        RewardPackage.objects.create(name='Bronze', deposit_threshold=Decimal('1000'), commission_rate_bonus=Decimal('2.00'))
        self._deposit('600')
        self.assertEqual(self.user.commission_rate, Decimal('0'))
        self._deposit('400')  # lifetime total now exactly 1000
        self.assertEqual(self.user.commission_rate, Decimal('2.00'))

    def test_single_large_deposit_crosses_multiple_tiers_at_once(self):
        RewardPackage.objects.create(name='Bronze', deposit_threshold=Decimal('1000'), commission_rate_bonus=Decimal('2.00'))
        RewardPackage.objects.create(name='Silver', deposit_threshold=Decimal('5000'), commission_rate_bonus=Decimal('5.00'))
        RewardPackage.objects.create(name='Gold', deposit_threshold=Decimal('10000'), commission_rate_bonus=Decimal('10.00'))

        self._deposit('7000')  # crosses Bronze and Silver, not Gold

        self.assertEqual(self.user.commission_rate, Decimal('7.00'))  # 2 + 5
        self.assertEqual(UserRewardClaim.objects.count(), 2)
        self.assertEqual(set(UserRewardClaim.objects.values_list('package__name', flat=True)), {'Bronze', 'Silver'})

    def test_package_never_double_claimed(self):
        RewardPackage.objects.create(name='Bronze', deposit_threshold=Decimal('1000'), commission_rate_bonus=Decimal('2.00'))
        self._deposit('1000')
        self.assertEqual(self.user.commission_rate, Decimal('2.00'))

        self._deposit('5000')  # well past the threshold again
        self.assertEqual(self.user.commission_rate, Decimal('2.00'))  # unchanged - already claimed
        self.assertEqual(UserRewardClaim.objects.count(), 1)

    def test_inactive_package_does_not_trigger(self):
        RewardPackage.objects.create(
            name='Disabled', deposit_threshold=Decimal('100'), commission_rate_bonus=Decimal('50.00'), is_active=False,
        )
        self._deposit('1000')
        self.assertEqual(self.user.commission_rate, Decimal('0'))
        self.assertEqual(UserRewardClaim.objects.count(), 0)

    def test_editing_package_bonus_does_not_change_past_claims(self):
        package = RewardPackage.objects.create(name='Bronze', deposit_threshold=Decimal('1000'), commission_rate_bonus=Decimal('2.00'))
        self._deposit('1000')
        self.assertEqual(self.user.commission_rate, Decimal('2.00'))

        package.commission_rate_bonus = Decimal('20.00')
        package.save(update_fields=['commission_rate_bonus'])

        claim = UserRewardClaim.objects.get()
        self.assertEqual(claim.commission_rate_bonus_applied, Decimal('2.00'))  # unchanged, frozen at claim time
        self.user.refresh_from_db()
        self.assertEqual(self.user.commission_rate, Decimal('2.00'))  # unchanged

    def test_get_lifetime_deposit_total(self):
        deposit_funds(self.user, Decimal('300'))
        deposit_funds(self.user, Decimal('450'))
        self.wallet.refresh_from_db()
        self.assertEqual(get_lifetime_deposit_total(self.wallet), Decimal('750'))

    def test_boosted_commission_rate_actually_pays_out_more(self):
        """
        Integration check: the reward isn't just a number that changes - it
        must actually change what the affiliate-commission system pays out
        the next time this user's child loses a bet.
        """
        RewardPackage.objects.create(name='Bronze', deposit_threshold=Decimal('1000'), commission_rate_bonus=Decimal('8.00'))
        self._deposit('1000')
        self.assertEqual(self.user.commission_rate, Decimal('8.00'))

        child = User.objects.create_user(email='rewardchild@example.com', password='testpass123', parent=self.user)
        Wallet.objects.get_or_create(user=child)
        deposit_funds(child, Decimal('500'))

        sport = Sport.objects.create(name='RewardTestSport', slug='reward-test-sport')
        match = Match.objects.create(
            sport=sport, home_team='H', away_team='A',
            start_time=timezone.now() + timezone.timedelta(hours=1),
            odds_home=Decimal('2.00'), odds_draw=Decimal('3.00'), odds_away=Decimal('4.00'),
        )
        self.wallet.refresh_from_db()
        balance_before_commission = self.wallet.balance
        self.assertEqual(balance_before_commission, Decimal('1000'))  # from their own qualifying deposit

        bet = place_bet(child, match.id, 'home', Decimal('100'))
        settle_bet(bet, 'away')  # child loses -> parent (self.user) gets 8% of 100 = 8

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, balance_before_commission + Decimal('8'))
