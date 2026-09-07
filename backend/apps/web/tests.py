import io
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from apps.audit.models import AuditLog
from apps.bets.models import Bet
from apps.bets.services import place_bet
from apps.cashback.models import CashbackCredit
from apps.exchange.models import ExchangeFill, ExchangeOrder
from apps.payments.models import CryptoPayment
from apps.sports.models import Match, RealtimeOddsConfig, RefreshMode, Sport
from apps.sports.pricing import normalize_odds
from apps.wallet.models import Wallet
from apps.wallet.services import deposit_funds

User = get_user_model()


def _read_xlsx_rows(response):
    """Returns (header_row, data_rows) from an .xlsx HttpResponse's content."""
    wb = load_workbook(io.BytesIO(response.content))
    ws = wb.active
    all_rows = list(ws.iter_rows(values_only=True))
    return all_rows[0], all_rows[1:]


class ExcelExportTestsBase(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email='exportadmin@example.com', password='testpass123', is_staff=True,
        )


class ManageUsersExportTests(ExcelExportTestsBase):
    def setUp(self):
        super().setUp()
        self.matching = User.objects.create_user(email='alice@example.com', password='testpass123')
        self.other = User.objects.create_user(email='bob@example.com', password='testpass123')

    def test_filtered_export_only_includes_matching_users(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('web:admin_users'), {'export': 'users_xlsx', 'q': 'alice'})
        self.assertEqual(
            response['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        self.assertIn('attachment', response['Content-Disposition'])
        header, rows = _read_xlsx_rows(response)
        emails = [row[0] for row in rows]
        self.assertIn('alice@example.com', emails)
        self.assertNotIn('bob@example.com', emails)

    def test_master_export_ignores_current_filters(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('web:admin_users'), {'export': 'users_master_xlsx', 'q': 'alice'})
        header, rows = _read_xlsx_rows(response)
        emails = [row[0] for row in rows]
        # A search for "alice" is active in the query string but must have no
        # effect on the master export - it should still include bob.
        self.assertIn('alice@example.com', emails)
        self.assertIn('bob@example.com', emails)

    def test_master_export_never_includes_password_hash(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('web:admin_users'), {'export': 'users_master_xlsx'})
        header, rows = _read_xlsx_rows(response)
        self.assertNotIn('Password', header)
        for row in rows:
            for cell in row:
                if isinstance(cell, str):
                    self.assertNotIn('pbkdf2', cell.lower())


class MasterExportAccessBoundaryTests(TestCase):
    def test_master_role_admin_export_only_includes_own_children(self):
        """
        The single most important guarantee of this feature: a Master-role
        admin's "master" export must only ever contain their own downstream
        users, never the whole platform - reopening that boundary would be
        the same class of privilege-escalation bug already fixed once in
        this project.
        """
        master = User.objects.create_user(
            email='master1@example.com', password='testpass123', role=User.Role.MASTER,
        )
        child = User.objects.create_user(
            email='child1@example.com', password='testpass123', parent=master,
        )
        unrelated = User.objects.create_user(
            email='unrelated@example.com', password='testpass123',
        )

        self.client.force_login(master)
        response = self.client.get(reverse('web:admin_users'), {'export': 'users_master_xlsx'})
        header, rows = _read_xlsx_rows(response)
        emails = [row[0] for row in rows]
        self.assertIn('child1@example.com', emails)
        self.assertNotIn('unrelated@example.com', emails)
        self.assertNotIn('master1@example.com', emails)


class LedgerExportTests(ExcelExportTestsBase):
    def setUp(self):
        super().setUp()
        self.target = User.objects.create_user(email='ledgertarget@example.com', password='testpass123')
        Wallet.objects.get_or_create(user=self.target)
        deposit_funds(self.target, Decimal('1000'))
        sport = Sport.objects.create(name='LedgerExportSport', slug='ledger-export-sport')
        self.match = Match.objects.create(
            sport=sport, home_team='LX Home', away_team='LX Away',
            start_time=timezone.now() + timezone.timedelta(hours=1), status=Match.Status.SCHEDULED,
            odds_home=Decimal('2.00'), odds_draw=Decimal('3.00'), odds_away=Decimal('4.00'),
        )
        self.bet = place_bet(self.target, self.match.id, 'home', Decimal('10'))
        CryptoPayment.objects.create(
            user=self.target, amount=Decimal('50'), currency='USDT',
            payment_type=CryptoPayment.PaymentType.DEPOSIT, status=CryptoPayment.Status.COMPLETED,
            provider='mock', external_id='ledgertest-1',
        )

    def test_bets_xlsx_export(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse('web:admin_user_ledger', args=[self.target.id]), {'export': 'bets_xlsx'},
        )
        header, rows = _read_xlsx_rows(response)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][2], 'home')
        self.assertEqual(rows[0][3], '2.00')

    def test_payments_xlsx_export(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse('web:admin_user_ledger', args=[self.target.id]), {'export': 'payments_xlsx'},
        )
        header, rows = _read_xlsx_rows(response)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], 'deposit')
        self.assertEqual(rows[0][7], 'ledgertest-1')


class ExchangeExportTests(ExcelExportTestsBase):
    def setUp(self):
        super().setUp()
        self.user1 = User.objects.create_user(email='exchangeuser1@example.com', password='testpass123')
        self.user2 = User.objects.create_user(email='exchangeuser2@example.com', password='testpass123')
        sport = Sport.objects.create(name='ExchangeExportSport', slug='exchange-export-sport')
        self.match = Match.objects.create(
            sport=sport, home_team='EX Home', away_team='EX Away',
            start_time=timezone.now() + timezone.timedelta(hours=1), status=Match.Status.SCHEDULED,
        )
        self.back_order = ExchangeOrder.objects.create(
            user=self.user1, match=self.match, selection=Bet.Selection.HOME, side=ExchangeOrder.Side.BACK,
            odds=Decimal('2.00'), stake=Decimal('10'), matched_stake=Decimal('10'),
            status=ExchangeOrder.Status.SETTLED,
        )
        self.lay_order = ExchangeOrder.objects.create(
            user=self.user2, match=self.match, selection=Bet.Selection.HOME, side=ExchangeOrder.Side.LAY,
            odds=Decimal('2.00'), stake=Decimal('10'), matched_stake=Decimal('10'),
            status=ExchangeOrder.Status.SETTLED,
        )
        ExchangeFill.objects.create(
            match=self.match, selection=Bet.Selection.HOME, back_order=self.back_order, lay_order=self.lay_order,
            odds=Decimal('2.00'), stake=Decimal('10'), status=ExchangeFill.Status.BACK_WON,
            commission_amount=Decimal('0.50'),
        )

    def test_orders_xlsx_export(self):
        self.client.force_login(self.admin)
        # orders_status defaults to "open" on this page (matching the on-screen
        # default view); pass it explicitly empty to get "any status", since
        # both test orders are SETTLED.
        response = self.client.get(
            reverse('web:admin_exchange'), {'export': 'orders_xlsx', 'orders_status': ''},
        )
        header, rows = _read_xlsx_rows(response)
        emails = [row[0] for row in rows]
        self.assertIn('exchangeuser1@example.com', emails)
        self.assertIn('exchangeuser2@example.com', emails)

    def test_fills_xlsx_export(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('web:admin_exchange'), {'export': 'fills_xlsx'})
        header, rows = _read_xlsx_rows(response)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][2], 'exchangeuser1@example.com')
        self.assertEqual(rows[0][3], 'exchangeuser2@example.com')
        self.assertEqual(rows[0][7], '0.50000000')


class CashbackExportTests(ExcelExportTestsBase):
    def setUp(self):
        super().setUp()
        self.target = User.objects.create_user(email='cashbackuser@example.com', password='testpass123')
        CashbackCredit.objects.create(
            user=self.target, source_description='Lost single bet #1', loss_amount=Decimal('20'),
            rate_applied=Decimal('10.00'), cashback_amount=Decimal('2'), multiplier_applied=Decimal('3.00'),
            wagering_required=Decimal('6'), wagering_progress=Decimal('0'), deduct_original_on_unlock=False,
            status=CashbackCredit.Status.LOCKED,
        )

    def test_credits_xlsx_export(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('web:admin_cashback'), {'export': 'credits_xlsx'})
        header, rows = _read_xlsx_rows(response)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], 'cashbackuser@example.com')
        self.assertEqual(rows[0][2], '20.00000000')


class AuditLogExportTests(ExcelExportTestsBase):
    def setUp(self):
        super().setUp()
        self.target = User.objects.create_user(email='audituser@example.com', password='testpass123')
        AuditLog.objects.create(
            user=self.target, action='test_action', target_type='Match', target_id='5',
            metadata={'note': 'test'}, ip_address='127.0.0.1',
        )

    def test_audit_xlsx_export(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('web:admin_audit'), {'export': 'xlsx'})
        header, rows = _read_xlsx_rows(response)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], 'test_action')
        self.assertIn('note', rows[0][4])


class RefreshModeTests(TestCase):
    def setUp(self):
        cache.clear()
        RealtimeOddsConfig.objects.filter(pk=1).delete()
        self.config = RealtimeOddsConfig.objects.create(
            pk=1, is_enabled=True, live_refresh_seconds=20,
            # Deliberately high so the VIEWER_GATED default never
            # accidentally triggers with just this test client's own
            # single heartbeat - isolates "is ALWAYS actually bypassing
            # the gate" from "did the gate happen to be satisfied anyway".
            min_viewers_for_realtime=5,
        )
        self.sport = Sport.objects.create(
            name='RefreshModeSport', slug='refresh-mode-sport', provider_key='refresh_mode_sport',
        )
        self.match = Match.objects.create(
            sport=self.sport, home_team='H', away_team='A',
            start_time=timezone.now() + timezone.timedelta(hours=1), status=Match.Status.SCHEDULED,
            odds_home=Decimal('2.00'), odds_draw=Decimal('3.00'), odds_away=Decimal('4.00'),
        )

    def _mock_provider(self):
        provider = MagicMock()
        provider.fetch_matches.return_value = [{
            'home_team': 'H', 'away_team': 'A',
            'start_time': self.match.start_time.isoformat(),
            'odds_home': '2.10', 'odds_draw': '3.10', 'odds_away': '4.10',
        }]
        return provider

    def _expected_refreshed_home_odds(self):
        # maybe_refresh_sport_odds applies normalize_odds's 5% margin
        # before storing - the stored value is never the raw provider
        # quote verbatim, matching this project's other realtime tests.
        home, _, _ = normalize_odds('2.10', '3.10', '4.10', margin=Decimal('0.05'))
        return home

    def test_viewer_gated_default_does_not_refresh_with_too_few_viewers(self):
        with patch('apps.sports.realtime.get_odds_provider', return_value=self._mock_provider()):
            response = self.client.get(reverse('web:match_odds_poll', args=[self.match.id]))
        data = response.json()
        self.match.refresh_from_db()
        self.assertEqual(self.match.odds_home, Decimal('2.00'))  # unchanged - not enough viewers
        self.assertTrue(data['should_continue_polling'])
        self.assertFalse(data['manual_refresh_available'])

    def test_always_mode_refreshes_regardless_of_viewer_count(self):
        self.match.refresh_mode_override = RefreshMode.ALWAYS
        self.match.save(update_fields=['refresh_mode_override'])
        with patch('apps.sports.realtime.get_odds_provider', return_value=self._mock_provider()):
            self.client.get(reverse('web:match_odds_poll', args=[self.match.id]))
        self.match.refresh_from_db()
        self.assertEqual(self.match.odds_home, self._expected_refreshed_home_odds())  # refreshed even with too few viewers

    def test_manual_only_mode_never_auto_refreshes(self):
        self.match.refresh_mode_override = RefreshMode.MANUAL_ONLY
        self.match.save(update_fields=['refresh_mode_override'])
        with patch('apps.sports.realtime.get_odds_provider', return_value=self._mock_provider()):
            response = self.client.get(reverse('web:match_odds_poll', args=[self.match.id]))
        data = response.json()
        self.match.refresh_from_db()
        self.assertEqual(self.match.odds_home, Decimal('2.00'))  # unchanged
        self.assertFalse(data['should_continue_polling'])  # poll loop stops itself, no JS changes needed
        self.assertTrue(data['manual_refresh_available'])

    def test_manual_only_mode_refreshes_on_explicit_request(self):
        self.match.refresh_mode_override = RefreshMode.MANUAL_ONLY
        self.match.save(update_fields=['refresh_mode_override'])
        with patch('apps.sports.realtime.get_odds_provider', return_value=self._mock_provider()):
            response = self.client.get(
                reverse('web:match_odds_poll', args=[self.match.id]), {'refresh': 'manual'},
            )
        self.match.refresh_from_db()
        expected = self._expected_refreshed_home_odds()
        self.assertEqual(self.match.odds_home, expected)
        self.assertEqual(response.json()['odds_home'], str(expected))

    def test_manual_only_mode_manual_refresh_is_still_throttled(self):
        self.match.refresh_mode_override = RefreshMode.MANUAL_ONLY
        self.match.save(update_fields=['refresh_mode_override'])
        provider = self._mock_provider()
        with patch('apps.sports.realtime.get_odds_provider', return_value=provider):
            self.client.get(reverse('web:match_odds_poll', args=[self.match.id]), {'refresh': 'manual'})
            self.client.get(reverse('web:match_odds_poll', args=[self.match.id]), {'refresh': 'manual'})
        provider.fetch_matches.assert_called_once()  # second click in the same window is throttled

    def test_stopped_mode_refuses_even_a_manual_refresh(self):
        self.match.refresh_mode_override = RefreshMode.STOPPED
        self.match.save(update_fields=['refresh_mode_override'])
        with patch('apps.sports.realtime.get_odds_provider', return_value=self._mock_provider()) as mock_get:
            response = self.client.get(
                reverse('web:match_odds_poll', args=[self.match.id]), {'refresh': 'manual'},
            )
        self.match.refresh_from_db()
        self.assertEqual(self.match.odds_home, Decimal('2.00'))
        mock_get.assert_not_called()
        data = response.json()
        self.assertFalse(data['should_continue_polling'])
        self.assertFalse(data['manual_refresh_available'])
