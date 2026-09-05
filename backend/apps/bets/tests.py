from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.sports.models import Sport, Match
from apps.wallet.models import Wallet
from apps.wallet.services import deposit_funds
from .models import Bet
from .services import place_bet, settle_bet

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
        self.wallet = Wallet.objects.get(user=self.user)
        self.assertEqual(self.wallet.balance, Decimal('110'))

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
        self.wallet = Wallet.objects.get(user=self.user)
        self.assertEqual(self.wallet.balance, Decimal('90'))
