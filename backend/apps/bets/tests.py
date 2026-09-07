from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase, APIClient

from apps.accounts.models import User
from apps.sports.models import Sport, Match, UserMatchOddsOverride
from apps.wallet.models import Wallet, WalletTransaction
from apps.wallet.services import deposit_funds
from .models import Bet, ParlayBet
from .services import settle_bet, place_bet, place_parlay_bet, settle_bets_for_match

User = get_user_model()


class BettingServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='bets@example.com',
            password='testpass',
            min_bet_amount=Decimal('1'),
            max_bet_amount=Decimal('50'),
        )
        deposit_funds(self.user, Decimal('100'))
        self.sport = Sport.objects.create(name='Football', slug='football')
        self.match = Match.objects.create(
            sport=self.sport,
            home_team='Home',
            away_team='Away',
            odds_home=Decimal('2.0'),
            odds_draw=Decimal('3.5'),
            odds_away=Decimal('4.0'),
            start_time=timezone.now() + timedelta(days=1),
        )

    def test_place_bet_success(self):
        bet = place_bet(
            user=self.user,
            match_id=self.match.id,
            selection=Bet.Selection.HOME,
            stake=Decimal('10'),
        )
        self.assertEqual(bet.status, Bet.Status.PENDING)
        self.wallet = Wallet.objects.get(user=self.user)
        self.assertEqual(self.wallet.balance, Decimal('90'))

    def test_place_bet_minimum_limit(self):
        with self.assertRaises(Exception):
            place_bet(
                user=self.user,
                match_id=self.match.id,
                selection=Bet.Selection.HOME,
                stake=Decimal('0.5'),
            )

    def test_place_bet_maximum_limit(self):
        with self.assertRaises(Exception):
            place_bet(
                user=self.user,
                match_id=self.match.id,
                selection=Bet.Selection.HOME,
                stake=Decimal('51'),
            )

    def test_settle_win(self):
        bet = place_bet(
            user=self.user,
            match_id=self.match.id,
            selection=Bet.Selection.HOME,
            stake=Decimal('10'),
        )
        settle_bet(bet, Bet.Selection.HOME)
        bet.refresh_from_db()
        self.assertEqual(bet.status, Bet.Status.WON)
        wallet = Wallet.objects.get(user=self.user)
        self.assertEqual(wallet.balance, Decimal('110'))

    def test_settle_loss(self):
        bet = place_bet(
            user=self.user,
            match_id=self.match.id,
            selection=Bet.Selection.HOME,
            stake=Decimal('10'),
        )
        settle_bet(bet, Bet.Selection.AWAY)
        bet.refresh_from_db()
        self.assertEqual(bet.status, Bet.Status.LOST)
        self.assertEqual(bet.stake, Decimal('10'))
        wallet = Wallet.objects.get(user=self.user)
        self.assertEqual(wallet.balance, Decimal('90'))

    def test_commission_credited_on_loss(self):
        # Create a parent with 10% commission rate
        parent = User.objects.create_user(
            email='parent@example.com',
            password='parentpass',
            role=User.Role.AGENT,
            commission_rate=Decimal('10'),
        )
        # A Wallet is auto-created via signal when the User is created.

        # Make our main user a child of the parent
        self.user.parent = parent
        self.user.save()

        bet = place_bet(
            user=self.user,
            match_id=self.match.id,
            selection=Bet.Selection.HOME,
            stake=Decimal('10'),
        )
        settle_bet(bet, Bet.Selection.AWAY)   # losing bet

        parent_wallet = Wallet.objects.get(user=parent)
        self.assertEqual(parent_wallet.balance, Decimal('1'))   # 10% of 10

        txn = WalletTransaction.objects.filter(
            wallet=parent_wallet,
            txn_type=WalletTransaction.TxnType.COMMISSION,
        ).first()
        self.assertIsNotNone(txn)
        self.assertEqual(txn.amount, Decimal('1'))


class PerUserOddsOverrideBetPlacementTests(TestCase):
    def setUp(self):
        self.sport = Sport.objects.create(name='PerUserBetSport', slug='per-user-bet-sport')
        self.match = Match.objects.create(
            sport=self.sport, home_team='H', away_team='A',
            start_time=timezone.now() + timedelta(hours=1), status=Match.Status.SCHEDULED,
            odds_home=Decimal('2.00'), odds_draw=Decimal('3.00'), odds_away=Decimal('4.00'),
        )
        self.targeted_user = User.objects.create_user(email='targeted@example.com', password='testpass123')
        deposit_funds(self.targeted_user, Decimal('1000'))
        self.other_user = User.objects.create_user(email='untargeted@example.com', password='testpass123')
        deposit_funds(self.other_user, Decimal('1000'))

    def test_targeted_users_bet_is_priced_and_recorded_at_the_overridden_rate(self):
        UserMatchOddsOverride.objects.create(
            user=self.targeted_user, match=self.match, adjustment=Decimal('-0.50'),
        )
        bet = place_bet(
            user=self.targeted_user, match_id=self.match.id, selection=Bet.Selection.HOME, stake=Decimal('10'),
        )
        self.assertEqual(bet.odds, Decimal('1.50'))
        self.assertEqual(bet.potential_payout, Decimal('15.00'))

    def test_untargeted_users_bet_on_the_same_match_uses_the_standard_rate(self):
        UserMatchOddsOverride.objects.create(
            user=self.targeted_user, match=self.match, adjustment=Decimal('-0.50'),
        )
        bet = place_bet(
            user=self.other_user, match_id=self.match.id, selection=Bet.Selection.HOME, stake=Decimal('10'),
        )
        self.assertEqual(bet.odds, Decimal('2.00'))

    def test_user_global_override_applies_across_matches_at_bet_placement(self):
        self.targeted_user.odds_adjustment_override = Decimal('1.00')
        self.targeted_user.save(update_fields=['odds_adjustment_override'])
        bet = place_bet(
            user=self.targeted_user, match_id=self.match.id, selection=Bet.Selection.AWAY, stake=Decimal('5'),
        )
        self.assertEqual(bet.odds, Decimal('5.00'))  # 4.00 + 1.00

    def test_matches_own_odds_field_is_never_mutated_by_a_per_user_override(self):
        UserMatchOddsOverride.objects.create(
            user=self.targeted_user, match=self.match, adjustment=Decimal('2.00'),
        )
        place_bet(user=self.targeted_user, match_id=self.match.id, selection=Bet.Selection.HOME, stake=Decimal('10'))
        self.match.refresh_from_db()
        self.assertEqual(self.match.odds_home, Decimal('2.00'))  # unchanged


class ParlayAPITests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='parlayuser@example.com',
            password='parlaypass123',
            min_bet_amount=Decimal('1'),
            max_bet_amount=Decimal('100'),
        )
        deposit_funds(self.user, Decimal('200'))
        self.client.force_authenticate(user=self.user)

        self.sport = Sport.objects.create(name='Football', slug='football')
        now = timezone.now()
        self.match1 = Match.objects.create(
            sport=self.sport,
            home_team='Home1',
            away_team='Away1',
            start_time=now + timedelta(days=1),
            odds_home=Decimal('2.0'),
            odds_draw=Decimal('3.5'),
            odds_away=Decimal('4.0'),
        )
        self.match2 = Match.objects.create(
            sport=self.sport,
            home_team='Home2',
            away_team='Away2',
            start_time=now + timedelta(days=2),
            odds_home=Decimal('1.5'),
            odds_draw=Decimal('4.0'),
            odds_away=Decimal('6.0'),
        )

    def test_place_parlay_via_api(self):
        response = self.client.post(
            '/api/parlays/place/',
            {
                'stake': '20',
                'selections': [
                    {'match': self.match1.id, 'selection': Bet.Selection.HOME},
                    {'match': self.match2.id, 'selection': Bet.Selection.HOME},
                ],
            },
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['status'], ParlayBet.Status.PENDING)
        wallet = Wallet.objects.get(user=self.user)
        self.assertEqual(wallet.balance, Decimal('180'))

    def test_parlay_settles_as_won(self):
        parlay = place_parlay_bet(
            user=self.user,
            stake=Decimal('20'),
            selections=[
                {'match': self.match1.id, 'selection': Bet.Selection.HOME},
                {'match': self.match2.id, 'selection': Bet.Selection.HOME},
            ],
        )

        self.match1.status = Match.Status.FINISHED
        self.match1.home_score = 2
        self.match1.away_score = 0
        self.match1.save()
        self.match2.status = Match.Status.FINISHED
        self.match2.home_score = 3
        self.match2.away_score = 1
        self.match2.save()

        settle_bets_for_match(self.match1)
        settle_bets_for_match(self.match2)

        parlay.refresh_from_db()
        self.assertEqual(parlay.status, ParlayBet.Status.WON)
        wallet = Wallet.objects.get(user=self.user)
        self.assertEqual(wallet.balance, Decimal('240'))

    def test_parlay_settles_as_lost(self):
        parlay = place_parlay_bet(
            user=self.user,
            stake=Decimal('20'),
            selections=[
                {'match': self.match1.id, 'selection': Bet.Selection.HOME},
                {'match': self.match2.id, 'selection': Bet.Selection.HOME},
            ],
        )

        self.match1.status = Match.Status.FINISHED
        self.match1.home_score = 2
        self.match1.away_score = 0
        self.match1.save()
        self.match2.status = Match.Status.FINISHED
        self.match2.home_score = 0
        self.match2.away_score = 1
        self.match2.save()

        settle_bets_for_match(self.match1)
        settle_bets_for_match(self.match2)

        parlay.refresh_from_db()
        self.assertEqual(parlay.status, ParlayBet.Status.LOST)
        wallet = Wallet.objects.get(user=self.user)
        self.assertEqual(wallet.balance, Decimal('180'))
