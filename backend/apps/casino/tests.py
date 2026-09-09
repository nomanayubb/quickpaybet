from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.wallet.models import Wallet, WalletTransaction
from apps.wallet.services import deposit_funds

from .admin import CasinoBrandAdmin, CasinoGameAdmin
from .models import CasinoBrand, CasinoConfig, CasinoGame, CasinoRoundSettlement, CasinoSession, CasinoWalletEvent
from .providers import MockCasinoProvider
from .services import (
    InsufficientCasinoBalance,
    handle_round_settlement,
    handle_waija_balance_query,
    handle_waija_wallet_event,
    launch_game,
)

User = get_user_model()


class CasinoServiceTests(TestCase):
    def setUp(self):
        CasinoConfig.objects.filter(pk=1).delete()
        self.config = CasinoConfig.objects.create(
            pk=1, is_enabled=True, max_launch_balance=Decimal('100.00'),
            provider_currency_code='PKR', usd_to_provider_currency_rate=Decimal('280.0000'),
        )
        self.brand = CasinoBrand.objects.create(brand_id=1, name='Pragmatic Play', is_active=True)
        self.game = CasinoGame.objects.create(
            game_id=737, game_uid='737', brand=self.brand, name='Aviator', category='slots', is_active=True,
        )
        self.user = User.objects.create_user(email='casinouser@example.com', password='testpass123')
        self.wallet = Wallet.objects.get(user=self.user)
        deposit_funds(self.user, Decimal('1000'))
        self.wallet.refresh_from_db()

    def _patched_provider(self):
        return patch('apps.casino.services.get_casino_provider', return_value=MockCasinoProvider())

    def test_launch_disabled_rejected(self):
        self.config.is_enabled = False
        self.config.save(update_fields=['is_enabled'])
        with self._patched_provider():
            with self.assertRaises(Exception):
                launch_game(self.user, self.game, Decimal('10'), 'https://x/return', 'https://x/callback')

    def test_launch_exceeds_max_rejected(self):
        with self._patched_provider():
            with self.assertRaises(Exception):
                launch_game(self.user, self.game, Decimal('500'), 'https://x/return', 'https://x/callback')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('1000'))  # nothing was ever debited

    def test_launch_debits_wallet_and_converts_currency_exactly(self):
        # 10 USD * 280.0000 rate = exactly 2800.00 PKR - hand-computed, not
        # just algebra, per this project's standing money-math test rule.
        with self._patched_provider():
            session, url = launch_game(self.user, self.game, Decimal('10'), 'https://x/return', 'https://x/callback')

        self.assertTrue(url.startswith('https://mock-casino.local/play'))
        self.assertEqual(session.opened_balance_usd, Decimal('10'))
        self.assertEqual(session.opened_balance_provider_currency, Decimal('2800.00'))
        self.assertEqual(session.provider_currency_code, 'PKR')
        self.assertEqual(session.fx_rate_applied, Decimal('280.0000'))
        # config used a non-default (PKR) provider currency in this test to
        # prove the conversion math is still fully config-driven, not
        # hardcoded - see apps.casino.services.launch_game's docstring for
        # why the real default is USD (confirmed against the live account).
        self.assertEqual(session.status, CasinoSession.Status.OPEN)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('990'))  # 1000 - 10
        self.assertEqual(self.wallet.reserved_balance, Decimal('0'))  # reservation fully converted, not left dangling

        txn = WalletTransaction.objects.get(txn_type=WalletTransaction.TxnType.CASINO_SESSION_OPEN)
        self.assertEqual(txn.amount, Decimal('-10.00000000'))
        self.assertEqual(txn.balance_after, Decimal('990'))

    def test_launch_failure_releases_reservation(self):
        with patch('apps.casino.services.get_casino_provider') as mock_get_provider:
            mock_provider = MockCasinoProvider()
            mock_provider.launch_game = lambda **kwargs: (_ for _ in ()).throw(RuntimeError('provider down'))
            mock_get_provider.return_value = mock_provider
            with self.assertRaises(RuntimeError):
                launch_game(self.user, self.game, Decimal('10'), 'https://x/return', 'https://x/callback')

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('1000'))  # untouched
        self.assertEqual(self.wallet.reserved_balance, Decimal('0'))  # reservation released, not left dangling

    def test_round_settlement_credits_win_converted_back_to_usd(self):
        with self._patched_provider():
            session, _ = launch_game(self.user, self.game, Decimal('10'), 'https://x/return', 'https://x/callback')

        # win_amount reported in PKR (the session's provider currency);
        # 1400 PKR / 280.0000 rate = exactly 5.00000000 USD.
        handle_round_settlement(
            member_account=str(self.user.id), game_uid='737',
            bet_amount=280, win_amount=1400, credit_amount=1400,
            serial_number='round-1', raw_payload={'game_round': 'r1'},
        )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('995'))  # 990 + 5

        settlement = CasinoRoundSettlement.objects.get(provider_serial_number='round-1')
        self.assertEqual(settlement.session_id, session.id)
        self.assertEqual(settlement.win_amount, Decimal('1400.00000000'))

    def test_round_settlement_is_idempotent_on_duplicate_serial_number(self):
        with self._patched_provider():
            launch_game(self.user, self.game, Decimal('10'), 'https://x/return', 'https://x/callback')

        for _ in range(2):
            handle_round_settlement(
                member_account=str(self.user.id), game_uid='737',
                bet_amount=280, win_amount=1400, credit_amount=1400,
                serial_number='round-dup', raw_payload={},
            )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('995'))  # credited exactly once, not twice
        self.assertEqual(CasinoRoundSettlement.objects.filter(provider_serial_number='round-dup').count(), 1)

    def test_round_settlement_with_no_win_does_not_touch_balance(self):
        with self._patched_provider():
            launch_game(self.user, self.game, Decimal('10'), 'https://x/return', 'https://x/callback')

        handle_round_settlement(
            member_account=str(self.user.id), game_uid='737',
            bet_amount=280, win_amount=0, credit_amount=0,
            serial_number='round-loss', raw_payload={},
        )
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('990'))  # loss already accounted for at launch debit


class CasinoAdminBulkActionTests(TestCase):
    def setUp(self):
        self.brand = CasinoBrand.objects.create(brand_id=1, name='Brand A', is_active=True)
        self.other_brand = CasinoBrand.objects.create(brand_id=2, name='Brand B', is_active=True)
        self.game = CasinoGame.objects.create(game_id=1, game_uid='1', brand=self.brand, name='Game A', is_active=True)
        self.other_game = CasinoGame.objects.create(game_id=2, game_uid='2', brand=self.brand, name='Game B', is_active=True)

    def test_bulk_disable_only_touches_selected_brands(self):
        admin_instance = CasinoBrandAdmin(CasinoBrand, None)
        admin_instance.message_user = lambda *args, **kwargs: None
        admin_instance.disable_selected(None, CasinoBrand.objects.filter(pk=self.brand.pk))

        self.brand.refresh_from_db()
        self.other_brand.refresh_from_db()
        self.assertFalse(self.brand.is_active)
        self.assertTrue(self.other_brand.is_active)  # untouched

    def test_bulk_enable_only_touches_selected_games(self):
        CasinoGame.objects.update(is_active=False)
        admin_instance = CasinoGameAdmin(CasinoGame, None)
        admin_instance.message_user = lambda *args, **kwargs: None
        admin_instance.enable_selected(None, CasinoGame.objects.filter(pk=self.game.pk))

        self.game.refresh_from_db()
        self.other_game.refresh_from_db()
        self.assertTrue(self.game.is_active)
        self.assertFalse(self.other_game.is_active)  # untouched


class _MockSeamlessCasinoProvider(MockCasinoProvider):
    """A seamless-wallet mock (like Waija) for exercising launch_game's other branch, without needing real Waija credentials."""
    requires_upfront_balance = False


class SeamlessLaunchTests(TestCase):
    """launch_game() must never touch the wallet for a seamless-wallet provider (see providers.BaseCasinoProvider.requires_upfront_balance) - money only moves later, via wallet-event callbacks."""

    def setUp(self):
        CasinoConfig.objects.filter(pk=1).delete()
        self.config = CasinoConfig.objects.create(pk=1, is_enabled=True, max_launch_balance=Decimal('100.00'))
        self.brand = CasinoBrand.objects.create(brand_id=1, name='SlotsGateway', is_active=True)
        self.game = CasinoGame.objects.create(
            game_id=1, game_uid='onlyplay/SaintBananas', brand=self.brand, name='Saint Bananas', is_active=True,
        )
        self.user = User.objects.create_user(email='seamlessuser@example.com', password='testpass123')
        self.wallet = Wallet.objects.get(user=self.user)
        deposit_funds(self.user, Decimal('1000'))
        self.wallet.refresh_from_db()

    def test_launch_does_not_touch_wallet(self):
        with patch('apps.casino.services.get_casino_provider', return_value=_MockSeamlessCasinoProvider()):
            session, url = launch_game(self.user, self.game, None, 'https://x/return', 'https://x/callback')

        self.assertTrue(url.startswith('https://mock-casino.local/play'))
        self.assertEqual(session.opened_balance_usd, Decimal('0'))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('1000'))  # untouched
        self.assertEqual(self.wallet.reserved_balance, Decimal('0'))
        self.assertEqual(WalletTransaction.objects.filter(wallet=self.wallet).count(), 1)  # only the original deposit


class WaijaWalletEventServiceTests(TestCase):
    """
    Hand-computed cents<->USD conversions and idempotency for the true
    seamless-wallet callback path (Waija's debit/credit/balance events),
    per this project's standing rule to verify money math with exact
    numbers, not just algebra.
    """

    def setUp(self):
        self.user = User.objects.create_user(email='waijauser@example.com', password='testpass123')
        self.wallet = Wallet.objects.get(user=self.user)
        deposit_funds(self.user, Decimal('50'))
        self.wallet.refresh_from_db()
        self.username = f'qpb{self.user.id}'

    def test_balance_query_reports_current_balance_no_mutation(self):
        balance = handle_waija_balance_query(self.username)
        self.assertEqual(balance, Decimal('50'))
        self.assertEqual(CasinoWalletEvent.objects.count(), 0)

    def test_debit_reduces_balance_exactly(self):
        # $2.00 stake -> new balance must be exactly 48.00
        new_balance = handle_waija_wallet_event(
            username=self.username, action='debit', amount=Decimal('2.00'), call_id='call-1',
            round_id='round-1', game_uid='onlyplay/SaintBananas', is_rollback=False,
            event_type='spin', raw_payload={},
        )
        self.assertEqual(new_balance, Decimal('48.00'))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('48.00'))
        txn = WalletTransaction.objects.get(reference_id='casino-wallet-event-call-1')
        self.assertEqual(txn.amount, Decimal('-2.00'))
        self.assertEqual(txn.txn_type, WalletTransaction.TxnType.CASINO_BET)

    def test_credit_increases_balance_exactly(self):
        new_balance = handle_waija_wallet_event(
            username=self.username, action='credit', amount=Decimal('13.39'), call_id='call-2',
            round_id='round-1', game_uid='onlyplay/SaintBananas', is_rollback=False,
            event_type='spin', raw_payload={},
        )
        self.assertEqual(new_balance, Decimal('63.39'))  # 50 + 13.39, exact
        txn = WalletTransaction.objects.get(reference_id='casino-wallet-event-call-2')
        self.assertEqual(txn.txn_type, WalletTransaction.TxnType.CASINO_WIN)

    def test_debit_rejected_when_insufficient_balance_wallet_unchanged(self):
        with self.assertRaises(InsufficientCasinoBalance) as ctx:
            handle_waija_wallet_event(
                username=self.username, action='debit', amount=Decimal('999.00'), call_id='call-3',
                round_id='round-1', game_uid='x', is_rollback=False, event_type='spin', raw_payload={},
            )
        self.assertEqual(ctx.exception.balance, Decimal('50'))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('50'))  # untouched
        self.assertEqual(CasinoWalletEvent.objects.count(), 0)

    def test_duplicate_call_id_is_a_safe_no_op(self):
        for _ in range(2):
            handle_waija_wallet_event(
                username=self.username, action='debit', amount=Decimal('5.00'), call_id='call-dup',
                round_id='round-1', game_uid='x', is_rollback=False, event_type='spin', raw_payload={},
            )
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('45.00'))  # debited exactly once, not twice
        self.assertEqual(CasinoWalletEvent.objects.filter(call_id='call-dup').count(), 1)

    def test_free_round_debit_never_touches_cash(self):
        new_balance = handle_waija_wallet_event(
            username=self.username, action='debit', amount=Decimal('1.00'), call_id='call-fs-1',
            round_id='round-fs', game_uid='x', is_rollback=False, event_type='bonus_fs', raw_payload={},
        )
        self.assertEqual(new_balance, Decimal('50'))  # unchanged - it's a free spin
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('50'))
        event = CasinoWalletEvent.objects.get(call_id='call-fs-1')
        self.assertTrue(event.cash_skipped)
        self.assertEqual(WalletTransaction.objects.filter(reference_id='casino-wallet-event-call-fs-1').count(), 0)

    def test_free_round_win_credits_real_cash(self):
        new_balance = handle_waija_wallet_event(
            username=self.username, action='credit', amount=Decimal('7.50'), call_id='call-fs-2',
            round_id='round-fs', game_uid='x', is_rollback=False, event_type='bonus_fs', raw_payload={},
        )
        self.assertEqual(new_balance, Decimal('57.50'))  # a free-round win IS real cash
        event = CasinoWalletEvent.objects.get(call_id='call-fs-2')
        self.assertFalse(event.cash_skipped)

    def test_unknown_user_rejected(self):
        with self.assertRaises(Exception):
            handle_waija_wallet_event(
                username='qpb999999', action='debit', amount=Decimal('1.00'), call_id='call-4',
                round_id='r', game_uid='x', is_rollback=False, event_type='spin', raw_payload={},
            )


class WaijaWalletCallbackViewTests(TestCase):
    """End-to-end through the actual GET view, including signature/timestamp validation."""

    def setUp(self):
        import os
        os.environ['CASINO_PROVIDER'] = 'waija'
        os.environ['WAIJA_SALTKEY'] = 'test-saltkey'
        self.addCleanup(lambda: os.environ.pop('CASINO_PROVIDER', None))
        self.addCleanup(lambda: os.environ.pop('WAIJA_SALTKEY', None))

        self.user = User.objects.create_user(email='waijaviewuser@example.com', password='testpass123')
        self.wallet = Wallet.objects.get(user=self.user)
        deposit_funds(self.user, Decimal('20'))
        self.wallet.refresh_from_db()

    def _signed_params(self, **extra):
        import hashlib
        import time
        timestamp = str(int(time.time()))
        key = hashlib.md5(f'{timestamp}test-saltkey'.encode('utf-8')).hexdigest()
        params = {'timestamp': timestamp, 'key': key, 'username': f'qpb{self.user.id}'}
        params.update(extra)
        return params

    def test_balance_callback_returns_cents(self):
        response = self.client.get('/api/casino/waija/callback/', self._signed_params(action='balance'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'error': 0, 'balance': 2000})

    def test_debit_callback_applies_and_returns_new_balance_in_cents(self):
        response = self.client.get('/api/casino/waija/callback/', self._signed_params(
            action='debit', amount='500', call_id='view-call-1', round_id='r1', game_id='x', rb='0', type='spin',
        ))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'error': 0, 'balance': 1500})
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('15.00'))

    def test_invalid_signature_rejected_with_error_2(self):
        params = self._signed_params(action='balance')
        params['key'] = 'wrong-signature'
        response = self.client.get('/api/casino/waija/callback/', params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'error': 2, 'balance': 0})

    def test_stale_timestamp_rejected_with_error_2(self):
        params = self._signed_params(action='balance')
        params['timestamp'] = str(int(params['timestamp']) - 3600)  # over an hour old
        response = self.client.get('/api/casino/waija/callback/', params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'error': 2, 'balance': 0})
