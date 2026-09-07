import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase, APIClient

from apps.accounts.models import User
from apps.sports.models import Sport, Match
from apps.wallet.models import Wallet, WalletTransaction
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


TEST_BACKUP_KEY = Fernet.generate_key().decode()


class BackupRestoreTests(TestCase):
    """
    Real backup_database/restore_database round trip against real data - no
    mocking. BACKUP_DIR is overridden to a throwaway temp directory so this
    never touches the real backups/ folder or its files.
    """

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.settings_override = override_settings(
            BACKUP_DIR=Path(self.tmpdir.name), BACKUP_ENCRYPTION_KEY=TEST_BACKUP_KEY,
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)

        self.password = 'RoundTrip123!'
        self.user = User.objects.create_user(email='backup-roundtrip@example.com', password=self.password)
        self.wallet = Wallet.objects.get(user=self.user)
        deposit_funds(self.user, Decimal('250.5'))

        self.sport = Sport.objects.create(name='Backup Sport', slug='backup-sport')
        self.match = Match.objects.create(
            sport=self.sport, home_team='Backup Home', away_team='Backup Away',
            start_time=timezone.now() + timezone.timedelta(days=1),
            odds_home=Decimal('2.0'), odds_draw=Decimal('3.5'), odds_away=Decimal('4.0'),
        )
        self.bet = place_bet(self.user, self.match.id, Bet.Selection.HOME, Decimal('10'))

    def _run_backup(self):
        return Path(call_command('backup_database'))

    def test_backup_file_is_encrypted_not_plaintext(self):
        path = self._run_backup()
        self.assertTrue(path.exists())
        raw = path.read_bytes()

        self.assertNotIn(self.user.email.encode(), raw)
        self.assertNotIn(b'Backup Home', raw)

        with self.assertRaises(Exception):
            Fernet(Fernet.generate_key()).decrypt(raw)

        plaintext = Fernet(TEST_BACKUP_KEY.encode()).decrypt(raw)
        self.assertIn(self.user.email.encode(), plaintext)

    def test_backup_missing_key_raises(self):
        with override_settings(BACKUP_ENCRYPTION_KEY=''):
            with self.assertRaises(CommandError):
                call_command('backup_database')

    def test_restore_refuses_without_confirm(self):
        path = self._run_backup()
        with self.assertRaises(CommandError):
            call_command('restore_database', str(path))

    def test_full_round_trip_restores_users_wallets_and_bets(self):
        path = self._run_backup()

        stake_amount = self.bet.stake
        self.wallet.refresh_from_db()
        original_balance = self.wallet.balance
        original_bet_id = self.bet.id

        # Wipe every row the backup covers, in FK-safe order, then restore
        # into what's meant to look like a freshly migrated empty database.
        Bet.objects.filter(pk=original_bet_id).delete()
        WalletTransaction.objects.filter(wallet=self.wallet).delete()
        Wallet.objects.filter(pk=self.wallet.pk).delete()
        Match.objects.filter(pk=self.match.pk).delete()
        Sport.objects.filter(pk=self.sport.pk).delete()
        User.objects.filter(pk=self.user.pk).delete()

        self.assertFalse(User.objects.filter(email=self.user.email).exists())

        call_command('restore_database', str(path), confirm=True)

        restored_user = User.objects.get(email=self.user.email)
        self.assertTrue(restored_user.check_password(self.password))

        restored_wallet = Wallet.objects.get(user=restored_user)
        self.assertEqual(restored_wallet.balance, original_balance)

        restored_match = Match.objects.get(home_team='Backup Home', away_team='Backup Away')
        self.assertEqual(restored_match.odds_home, Decimal('2.0'))

        restored_bet = Bet.objects.get(user=restored_user, match=restored_match)
        self.assertEqual(restored_bet.stake, stake_amount)

    def test_retention_prunes_older_backups(self):
        for _ in range(3):
            call_command('backup_database', keep=2)
        remaining = sorted(Path(self.tmpdir.name).glob('backup_*.json.enc'))
        self.assertEqual(len(remaining), 2)


class BackupAdminPanelTests(TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.settings_override = override_settings(
            BACKUP_DIR=Path(self.tmpdir.name), BACKUP_ENCRYPTION_KEY=TEST_BACKUP_KEY,
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)

        self.admin = User.objects.create_user(
            email='backup-admin@example.com', password='AdminPass123!',
            is_staff=True, role=User.Role.ADMIN,
        )
        self.master = User.objects.create_user(
            email='backup-master@example.com', password='MasterPass123!',
            role=User.Role.MASTER,
        )
        self.client = Client()

    def test_non_full_admin_is_redirected(self):
        self.client.force_login(self.master)
        response = self.client.get(reverse('web:admin_backups'))
        self.assertEqual(response.status_code, 302)

    def test_full_admin_can_list_and_download(self):
        call_command('backup_database')
        filename = next(Path(self.tmpdir.name).glob('backup_*.json.enc')).name

        self.client.force_login(self.admin)
        list_response = self.client.get(reverse('web:admin_backups'))
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, filename)

        download_response = self.client.get(reverse('web:admin_backup_download', args=[filename]))
        self.assertEqual(download_response.status_code, 200)
        self.assertEqual(download_response.content, (Path(self.tmpdir.name) / filename).read_bytes())

    def test_trigger_backup_unreachable_broker_fails_fast_not_500(self):
        # Real bug caught live-testing this page with no local Celery/Redis
        # running: .delay() used to hang for minutes retrying the result
        # backend before this OperationalError path existed. This confirms
        # the view now degrades to a clean message instead of a 500 or a hang.
        self.client.force_login(self.admin)
        with patch('apps.common.tasks.run_database_backup.delay', side_effect=ConnectionError('refused')):
            response = self.client.post(reverse('web:admin_backups'), {'action': 'trigger'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Could not enqueue a backup')

    def test_trigger_backup_success_shows_enqueued_message(self):
        self.client.force_login(self.admin)
        with patch('apps.common.tasks.run_database_backup.delay') as mock_delay:
            response = self.client.post(reverse('web:admin_backups'), {'action': 'trigger'})
        mock_delay.assert_called_once()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Backup enqueued')

    def test_download_rejects_non_backup_filenames(self):
        # The URL pattern itself (`[^/]+`) already blocks any "/" in the
        # filename segment, so a literal "../" traversal never reaches the
        # view - this exercises the view's own prefix/suffix validation
        # instead, which is what stops e.g. a sibling file in BACKUP_DIR
        # from being downloaded through this endpoint.
        self.client.force_login(self.admin)
        response = self.client.get(reverse('web:admin_backup_download', args=['not-a-backup.txt']))
        self.assertEqual(response.status_code, 400)
