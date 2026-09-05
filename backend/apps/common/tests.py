from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase, APIClient

from apps.accounts.models import User
from apps.sports.models import Sport, Match
from apps.wallet.models import Wallet
from apps.wallet.services import deposit_funds
from apps.bets.models import Bet
from apps.bets.services import place_bet, settle_bets_for_match


class FullFlowAPITests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            email='admin@example.com',
            password='adminpass123'
        )
        self.admin_client = APIClient()
        self.admin_client.force_authenticate(user=self.admin)

        self.sport = Sport.objects.create(name='Football', slug='football')
        self.match = Match.objects.create(
            sport=self.sport,
            home_team='Home Team',
            away_team='Away Team',
            start_time=timezone.now() + timezone.timedelta(days=1),
            odds_home=Decimal('2.0'),
            odds_draw=Decimal('3.5'),
            odds_away=Decimal('4.0'),
        )

        self.player = User.objects.create_user(
            email='player@example.com',
            password='playerpass123',
            min_bet_amount=Decimal('1'),
            max_bet_amount=Decimal('50'),
        )
        self.player_client = APIClient()
        self.player_client.force_authenticate(user=self.player)

    def test_register_login_and_get_me(self):
        response = self.client.post(
            reverse('accounts:register'),
            {
                'email': 'newplayer@example.com',
                'password': 'NewPass123!',
                'password2': 'NewPass123!',
            },
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)

    def test_deposit_via_webhook(self):
        deposit_response = self.player_client.post(
            '/api/wallet/deposit/',
            {'amount': '100', 'currency': 'USDT'},
            format='json'
        )
        self.assertEqual(deposit_response.status_code, status.HTTP_201_CREATED)
        external_id = deposit_response.data['external_id']

        webhook_response = self.admin_client.post(
            '/api/payments/webhook/',
            {'external_id': external_id, 'status': 'completed'},
            format='json'
        )
        self.assertEqual(webhook_response.status_code, status.HTTP_200_OK)

        wallet = Wallet.objects.get(user=self.player)
        self.assertEqual(wallet.balance, Decimal('100'))

    def test_place_bet_and_settlement(self):
        # Deposit money
        deposit_funds(self.player, Decimal('100'))

        # Place a bet
        bet_response = self.player_client.post(
            '/api/bets/place/',
            {'match': self.match.id, 'selection': Bet.Selection.HOME, 'stake': '10'},
            format='json'
        )
        self.assertEqual(bet_response.status_code, status.HTTP_201_CREATED)

        # Wallet should be debited by stake
        wallet = Wallet.objects.get(user=self.player)
        self.assertEqual(wallet.balance, Decimal('90'))

        # Settle the match (home wins 2-0)
        self.match.status = Match.Status.FINISHED
        self.match.home_score = 2
        self.match.away_score = 0
        self.match.save()
        settle_bets_for_match(self.match)

        # Player wins: 90 + (10 * 2.0) = 110
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, Decimal('110'))

        bet = Bet.objects.get(user=self.player, match=self.match)
        self.assertEqual(bet.status, Bet.Status.WON)

    def test_admin_can_update_odds(self):
        response = self.admin_client.patch(
            f'/api/matches/{self.match.id}/update-odds/',
            {'odds_home': '1.5', 'odds_draw': '4.0', 'odds_away': '6.0'},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['odds_home'], '1.50')

        self.match.refresh_from_db()
        self.assertEqual(self.match.odds_home, Decimal('1.5'))
