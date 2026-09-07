import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.bets.services import place_bet
from apps.wallet.models import Wallet
from apps.wallet.services import deposit_funds

from .models import (
    BackMode, HouseLiquidityConfig, LayMode, Match, OddsAdjustmentConfig, OddsHistoryEntry,
    PricingOverride, RealtimeOddsConfig, RefreshMode, Sport, UserMatchOddsOverride,
)
from .pricing import (
    apply_odds_adjustment,
    compute_back_and_lay,
    compute_lay_price,
    get_effective_lay_reference_for_user,
    get_effective_odds_for_user,
    resolve_effective_adjustment,
    resolve_effective_lay_spread,
    resolve_match_pricing_mode,
    resolve_user_extra_adjustment,
    resolve_user_pricing_mode,
)
from .providers import (
    BaseOddsProvider,
    MockOddsProvider,
    ParlayAPIProvider,
    TheyOddsAPIProvider,
    get_odds_provider,
)
from .realtime import (
    get_effective_refresh_interval,
    maybe_refresh_sport_odds,
    record_viewer_heartbeat,
    resolve_effective_refresh_mode,
)

User = get_user_model()


def _mock_urlopen_returning(payload):
    """
    Builds a MagicMock standing in for urllib.request.urlopen(...) used as
    a context manager (`with urllib.request.urlopen(...) as response:`),
    returning `payload` as the JSON body. No real network call is ever made
    in these tests - this project's convention (mock external providers,
    never hit real services from automated tests).
    """
    response = MagicMock()
    response.read.return_value = json.dumps(payload).encode('utf-8')
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return MagicMock(return_value=response)


class ParlayAPIProviderTests(TestCase):
    def setUp(self):
        self.provider = ParlayAPIProvider()
        self.provider.api_key = 'test-key'
        self.provider.base_url = 'https://parlay-api.com/v1'
        self.provider.preferred_bookmaker = 'pinnacle'

    def test_fetch_sports_maps_fields(self):
        payload = [
            {'key': 'cricket_ipl', 'title': 'IPL', 'active': True, 'group': 'Cricket'},
            {'key': 'soccer_epl', 'title': 'EPL', 'active': True, 'group': 'Soccer'},
        ]
        with patch('apps.sports.providers.urllib.request.urlopen', _mock_urlopen_returning(payload)):
            result = self.provider.fetch_sports()

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], {'key': 'cricket_ipl', 'title': 'IPL', 'active': True, 'group': 'Cricket'})

    def test_fetch_matches_prefers_configured_bookmaker(self):
        payload = [{
            'home_team': 'Arsenal',
            'away_team': 'Chelsea',
            'commence_time': '2026-09-12T14:00:00Z',
            'sport_title': 'EPL',
            'bookmakers': [
                {'key': 'unibet', 'markets': [{'key': 'h2h', 'outcomes': [
                    {'name': 'Arsenal', 'price': 1.5}, {'name': 'Draw', 'price': 4.0}, {'name': 'Chelsea', 'price': 5.0},
                ]}]},
                {'key': 'pinnacle', 'markets': [{'key': 'h2h', 'outcomes': [
                    {'name': 'Arsenal', 'price': 1.9}, {'name': 'Draw', 'price': 3.6}, {'name': 'Chelsea', 'price': 3.9},
                ]}]},
            ],
        }]
        with patch('apps.sports.providers.urllib.request.urlopen', _mock_urlopen_returning(payload)):
            result = self.provider.fetch_matches(sport_key='soccer_epl')

        self.assertEqual(len(result), 1)
        # Must use pinnacle's prices, not unibet's (which appears first in the list).
        self.assertEqual(result[0]['odds_home'], '1.9')
        self.assertEqual(result[0]['odds_away'], '3.9')
        self.assertEqual(result[0]['odds_draw'], '3.6')

    def test_fetch_matches_falls_back_to_first_bookmaker_when_preferred_absent(self):
        payload = [{
            'home_team': 'Lakers',
            'away_team': 'Celtics',
            'commence_time': '2026-09-12T14:00:00Z',
            'sport_title': 'NBA',
            'bookmakers': [
                {'key': 'draftkings', 'markets': [{'key': 'h2h', 'outcomes': [
                    {'name': 'Lakers', 'price': 1.8}, {'name': 'Celtics', 'price': 2.0},
                ]}]},
            ],
        }]
        with patch('apps.sports.providers.urllib.request.urlopen', _mock_urlopen_returning(payload)):
            result = self.provider.fetch_matches(sport_key='basketball_nba')

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['odds_home'], '1.8')
        self.assertEqual(result[0]['odds_away'], '2.0')
        self.assertIsNone(result[0]['odds_draw'])

    def test_fetch_matches_skips_event_missing_home_or_away_price(self):
        payload = [{
            'home_team': 'Team A',
            'away_team': 'Team B',
            'commence_time': '2026-09-12T14:00:00Z',
            'sport_title': 'Test',
            'bookmakers': [
                {'key': 'pinnacle', 'markets': [{'key': 'h2h', 'outcomes': [
                    {'name': 'Team A', 'price': 1.8},
                ]}]},
            ],
        }]
        with patch('apps.sports.providers.urllib.request.urlopen', _mock_urlopen_returning(payload)):
            result = self.provider.fetch_matches(sport_key='some_sport')

        self.assertEqual(result, [])

    def test_fetch_matches_returns_empty_without_sport_key(self):
        self.assertEqual(self.provider.fetch_matches(sport_key=None), [])

    def test_fetch_scores_maps_completed_and_scores(self):
        payload = [{
            'home_team': 'Everton',
            'away_team': 'Manchester United',
            'commence_time': '2026-09-06T13:00:00Z',
            'sport_title': 'EPL',
            'completed': True,
            'scores': [{'name': 'Everton', 'score': '2'}, {'name': 'Manchester United', 'score': '2'}],
        }]
        with patch('apps.sports.providers.urllib.request.urlopen', _mock_urlopen_returning(payload)):
            result = self.provider.fetch_scores(sport_key='soccer_epl')

        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]['completed'])
        self.assertEqual(result[0]['home_score'], 2)
        self.assertEqual(result[0]['away_score'], 2)

    def test_fetch_scores_returns_empty_without_sport_key(self):
        self.assertEqual(self.provider.fetch_scores(sport_key=None), [])

    def test_missing_api_key_raises(self):
        self.provider.api_key = ''
        with self.assertRaises(RuntimeError):
            self.provider.fetch_sports()


class ProviderFactoryTests(TestCase):
    @override_settings()
    def test_get_odds_provider_returns_parlayapi(self):
        with patch.dict('os.environ', {'ODDS_PROVIDER': 'parlayapi'}):
            provider = get_odds_provider()
        self.assertIsInstance(provider, ParlayAPIProvider)
        self.assertEqual(provider.name, 'parlayapi')

    def test_get_odds_provider_still_returns_mock_by_default(self):
        with patch.dict('os.environ', {}, clear=False):
            import os
            os.environ.pop('ODDS_PROVIDER', None)
            provider = get_odds_provider()
        self.assertIsInstance(provider, MockOddsProvider)

    def test_get_odds_provider_still_returns_theoddsapi(self):
        with patch.dict('os.environ', {'ODDS_PROVIDER': 'theoddsapi'}):
            provider = get_odds_provider()
        self.assertIsInstance(provider, TheyOddsAPIProvider)


class InvalidSportKeysTests(TestCase):
    def test_base_default_is_empty(self):
        self.assertEqual(BaseOddsProvider.invalid_sport_keys, set())

    def test_theoddsapi_keeps_its_known_quirk(self):
        self.assertEqual(TheyOddsAPIProvider.invalid_sport_keys, {'football'})

    def test_parlayapi_has_no_known_quirk(self):
        self.assertEqual(ParlayAPIProvider.invalid_sport_keys, set())


class RealtimeOddsConfigTests(TestCase):
    def test_get_solo_creates_singleton_with_safe_defaults(self):
        config = RealtimeOddsConfig.get_solo()
        self.assertFalse(config.is_enabled)
        self.assertEqual(config.live_refresh_seconds, 20)
        self.assertEqual(RealtimeOddsConfig.objects.count(), 1)
        # Calling again must not create a second row.
        RealtimeOddsConfig.get_solo()
        self.assertEqual(RealtimeOddsConfig.objects.count(), 1)

    def test_out_of_range_value_rejected_by_full_clean(self):
        from django.core.exceptions import ValidationError as DjangoValidationError
        config = RealtimeOddsConfig(live_refresh_seconds=1)  # below the 5-second floor
        with self.assertRaises(DjangoValidationError):
            config.full_clean()


class RealtimeRefreshIntervalTests(TestCase):
    def setUp(self):
        self.config = RealtimeOddsConfig.get_solo()
        self.sport = Sport.objects.create(name='RtSport', slug='rt-sport', provider_key='rt_sport')

    def _match(self, status, start_time):
        return Match.objects.create(
            sport=self.sport, home_team='H', away_team='A',
            start_time=start_time, status=status,
        )

    def test_live_match_uses_live_interval(self):
        match = self._match(Match.Status.LIVE, timezone.now())
        self.assertEqual(get_effective_refresh_interval(match, self.config), self.config.live_refresh_seconds)

    def test_far_future_scheduled_match_uses_standard_interval(self):
        match = self._match(Match.Status.SCHEDULED, timezone.now() + timezone.timedelta(hours=5))
        self.assertEqual(get_effective_refresh_interval(match, self.config), self.config.not_started_refresh_seconds)

    def test_near_kickoff_scheduled_match_uses_proximity_boost(self):
        match = self._match(Match.Status.SCHEDULED, timezone.now() + timezone.timedelta(minutes=10))
        self.assertEqual(get_effective_refresh_interval(match, self.config), self.config.proximity_boost_seconds)

    def test_finished_match_has_no_interval(self):
        match = self._match(Match.Status.FINISHED, timezone.now() - timezone.timedelta(hours=2))
        self.assertIsNone(get_effective_refresh_interval(match, self.config))

    def test_cancelled_match_has_no_interval(self):
        match = self._match(Match.Status.CANCELLED, timezone.now())
        self.assertIsNone(get_effective_refresh_interval(match, self.config))


class ViewerHeartbeatTests(TestCase):
    def setUp(self):
        cache.clear()
        sport = Sport.objects.create(name='ViewerSport', slug='viewer-sport')
        self.match = Match.objects.create(
            sport=sport, home_team='H', away_team='A',
            start_time=timezone.now(), status=Match.Status.LIVE,
        )

    def test_single_viewer_counts_as_one(self):
        count = record_viewer_heartbeat(self.match, 'viewer-a', stale_after_seconds=60)
        self.assertEqual(count, 1)

    def test_multiple_distinct_viewers_accumulate(self):
        record_viewer_heartbeat(self.match, 'viewer-a', stale_after_seconds=60)
        record_viewer_heartbeat(self.match, 'viewer-b', stale_after_seconds=60)
        count = record_viewer_heartbeat(self.match, 'viewer-c', stale_after_seconds=60)
        self.assertEqual(count, 3)

    def test_same_viewer_heartbeating_again_does_not_double_count(self):
        record_viewer_heartbeat(self.match, 'viewer-a', stale_after_seconds=60)
        count = record_viewer_heartbeat(self.match, 'viewer-a', stale_after_seconds=60)
        self.assertEqual(count, 1)

    def test_stale_viewer_is_pruned(self):
        with patch('apps.sports.realtime.timezone') as mock_tz:
            mock_tz.now.return_value = timezone.now() - timezone.timedelta(seconds=120)
            record_viewer_heartbeat(self.match, 'viewer-old', stale_after_seconds=10)
        # Real time now (not mocked) - viewer-old is far older than the 10s staleness window.
        count = record_viewer_heartbeat(self.match, 'viewer-new', stale_after_seconds=10)
        self.assertEqual(count, 1)


class MaybeRefreshSportOddsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.sport = Sport.objects.create(name='ThrottleSport', slug='throttle-sport', provider_key='throttle_sport')
        self.match = Match.objects.create(
            sport=self.sport, home_team='Home Team', away_team='Away Team',
            start_time=timezone.now() + timezone.timedelta(hours=1), status=Match.Status.SCHEDULED,
        )

    def _mock_provider(self, odds_home='2.00', odds_draw='3.00', odds_away='4.00'):
        provider = MagicMock()
        provider.fetch_matches.return_value = [{
            'home_team': 'Home Team', 'away_team': 'Away Team',
            'start_time': self.match.start_time.isoformat(),
            'odds_home': odds_home, 'odds_draw': odds_draw, 'odds_away': odds_away,
        }]
        return provider

    def test_sport_without_provider_key_never_calls_provider(self):
        sport = Sport.objects.create(name='NoKeySport', slug='no-key-sport')
        with patch('apps.sports.realtime.get_odds_provider') as mock_get:
            result = maybe_refresh_sport_odds(sport, 20)
        self.assertFalse(result)
        mock_get.assert_not_called()

    def test_first_call_fetches_and_updates_match(self):
        from .pricing import normalize_odds

        provider = self._mock_provider()
        with patch('apps.sports.realtime.get_odds_provider', return_value=provider):
            result = maybe_refresh_sport_odds(self.sport, 20)

        self.assertTrue(result)
        provider.fetch_matches.assert_called_once_with(sport_key='throttle_sport')
        self.match.refresh_from_db()
        # maybe_refresh_sport_odds applies the same 5%-margin normalization
        # sync_odds already does - the DB value is the normalized odds, not
        # the raw provider value verbatim.
        expected_home, _, _ = normalize_odds('2.00', '3.00', '4.00', margin=Decimal('0.05'))
        self.assertEqual(self.match.odds_home, expected_home)

    def test_second_call_within_window_does_not_refetch(self):
        provider = self._mock_provider()
        with patch('apps.sports.realtime.get_odds_provider', return_value=provider):
            maybe_refresh_sport_odds(self.sport, 20)
            second_result = maybe_refresh_sport_odds(self.sport, 20)

        self.assertFalse(second_result)
        provider.fetch_matches.assert_called_once()  # not called a second time

    def test_history_not_recorded_when_toggle_off(self):
        provider = self._mock_provider()
        with patch('apps.sports.realtime.get_odds_provider', return_value=provider):
            maybe_refresh_sport_odds(self.sport, 20)
        self.assertEqual(OddsHistoryEntry.objects.count(), 0)

    def test_history_recorded_when_toggle_on(self):
        config = RealtimeOddsConfig.get_solo()
        config.store_full_odds_history = True
        config.save()

        provider = self._mock_provider()
        with patch('apps.sports.realtime.get_odds_provider', return_value=provider):
            maybe_refresh_sport_odds(self.sport, 20)

        entry = OddsHistoryEntry.objects.get()
        self.assertEqual(entry.match_id, self.match.id)
        self.match.refresh_from_db()
        self.assertEqual(entry.odds_home, self.match.odds_home)

    def test_no_matching_row_writes_no_history_even_when_enabled(self):
        config = RealtimeOddsConfig.get_solo()
        config.store_full_odds_history = True
        config.save()

        provider = MagicMock()
        provider.fetch_matches.return_value = [{
            'home_team': 'Nobody', 'away_team': 'Here',
            'start_time': self.match.start_time.isoformat(),
            'odds_home': '2.00', 'odds_draw': '3.00', 'odds_away': '4.00',
        }]
        with patch('apps.sports.realtime.get_odds_provider', return_value=provider):
            maybe_refresh_sport_odds(self.sport, 20)

        self.assertEqual(OddsHistoryEntry.objects.count(), 0)


class PlaceBetOddsRevalidationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='oddscheck@example.com', password='testpass123')
        Wallet.objects.get_or_create(user=self.user)
        deposit_funds(self.user, Decimal('1000'))
        sport = Sport.objects.create(name='RevalSport', slug='reval-sport')
        self.match = Match.objects.create(
            sport=sport, home_team='H', away_team='A',
            start_time=timezone.now() + timezone.timedelta(hours=1), status=Match.Status.SCHEDULED,
            odds_home=Decimal('2.00'), odds_draw=Decimal('3.00'), odds_away=Decimal('4.00'),
        )

    def test_no_odds_shown_skips_revalidation(self):
        bet = place_bet(self.user, self.match.id, 'home', Decimal('10'))
        self.assertEqual(bet.odds, Decimal('2.00'))

    def test_odds_shown_within_tolerance_accepted(self):
        # 2.00 actual vs 2.03 shown - well within the 2% tolerance.
        bet = place_bet(self.user, self.match.id, 'home', Decimal('10'), odds_shown=Decimal('2.03'))
        self.assertEqual(bet.odds, Decimal('2.00'))

    def test_odds_shown_beyond_tolerance_rejected(self):
        from rest_framework.exceptions import ValidationError
        # 2.00 actual vs 3.00 shown - a 50% move, well beyond tolerance.
        with self.assertRaises(ValidationError):
            place_bet(self.user, self.match.id, 'home', Decimal('10'), odds_shown=Decimal('3.00'))


class OddsAdjustmentPricingTests(TestCase):
    def test_zero_adjustment_is_a_no_op(self):
        result = apply_odds_adjustment(Decimal('0'), Decimal('2.00'), Decimal('3.00'), Decimal('4.00'))
        self.assertEqual(result, (Decimal('2.00'), Decimal('3.00'), Decimal('4.00')))

    def test_positive_adjustment_added_to_each_value(self):
        result = apply_odds_adjustment(Decimal('0.50'), Decimal('2.00'), Decimal('3.00'), Decimal('4.00'))
        self.assertEqual(result, (Decimal('2.50'), Decimal('3.50'), Decimal('4.50')))

    def test_negative_adjustment_subtracted_from_each_value(self):
        result = apply_odds_adjustment(Decimal('-0.50'), Decimal('2.00'), Decimal('3.00'), Decimal('4.00'))
        self.assertEqual(result, (Decimal('1.50'), Decimal('2.50'), Decimal('3.50')))

    def test_negative_adjustment_clamped_to_floor(self):
        # -10.00 against a short-priced favorite (1.20) would otherwise go
        # negative - this is the exact "database never gets corrupted"
        # guarantee the client asked for.
        home, draw, away = apply_odds_adjustment(Decimal('-10.00'), Decimal('1.20'), Decimal('3.00'), Decimal('4.00'))
        self.assertEqual(home, Decimal('1.01'))
        self.assertEqual(draw, Decimal('1.01'))
        self.assertEqual(away, Decimal('1.01'))

    def test_none_draw_passes_through_unchanged(self):
        # Two-outcome markets (no draw) must never crash on this.
        home, draw, away = apply_odds_adjustment(Decimal('1.00'), Decimal('2.00'), None, Decimal('4.00'))
        self.assertIsNone(draw)
        self.assertEqual(home, Decimal('3.00'))
        self.assertEqual(away, Decimal('5.00'))

    def test_resolve_effective_adjustment_prefers_match_override(self):
        self.assertEqual(
            resolve_effective_adjustment(Decimal('2.00'), Decimal('0.00')), Decimal('2.00'),
        )

    def test_resolve_effective_adjustment_falls_back_to_default(self):
        self.assertEqual(
            resolve_effective_adjustment(None, Decimal('1.50')), Decimal('1.50'),
        )


class OddsAdjustmentRealtimeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.sport = Sport.objects.create(name='AdjSport', slug='adj-sport', provider_key='adj_sport')

    def _mock_provider(self):
        provider = MagicMock()
        provider.fetch_matches.return_value = [{
            'home_team': 'Home Team', 'away_team': 'Away Team',
            'start_time': self.start_time.isoformat(),
            'odds_home': '2.00', 'odds_draw': '3.00', 'odds_away': '4.00',
        }]
        return provider

    def test_global_default_applied_when_match_has_no_override(self):
        from .pricing import normalize_odds

        OddsAdjustmentConfig.objects.create(pk=1, default_adjustment=Decimal('0.20'))
        self.start_time = timezone.now() + timezone.timedelta(hours=1)
        match = Match.objects.create(
            sport=self.sport, home_team='Home Team', away_team='Away Team',
            start_time=self.start_time, status=Match.Status.SCHEDULED,
        )

        with patch('apps.sports.realtime.get_odds_provider', return_value=self._mock_provider()):
            maybe_refresh_sport_odds(self.sport, 20)

        match.refresh_from_db()
        base_home, _, _ = normalize_odds('2.00', '3.00', '4.00', margin=Decimal('0.05'))
        self.assertEqual(match.odds_home, base_home + Decimal('0.20'))

    def test_per_match_override_takes_precedence_over_default(self):
        from .pricing import normalize_odds

        OddsAdjustmentConfig.objects.create(pk=1, default_adjustment=Decimal('0.20'))
        self.start_time = timezone.now() + timezone.timedelta(hours=1)
        match = Match.objects.create(
            sport=self.sport, home_team='Home Team', away_team='Away Team',
            start_time=self.start_time, status=Match.Status.SCHEDULED,
            odds_adjustment=Decimal('-1.00'),
        )

        with patch('apps.sports.realtime.get_odds_provider', return_value=self._mock_provider()):
            maybe_refresh_sport_odds(self.sport, 20)

        match.refresh_from_db()
        base_home, _, _ = normalize_odds('2.00', '3.00', '4.00', margin=Decimal('0.05'))
        self.assertEqual(match.odds_home, base_home - Decimal('1.00'))

    def test_existing_bet_unaffected_by_later_adjustment_change(self):
        user = User.objects.create_user(email='adjbet@example.com', password='testpass123')
        Wallet.objects.get_or_create(user=user)
        deposit_funds(user, Decimal('1000'))

        self.start_time = timezone.now() + timezone.timedelta(hours=1)
        match = Match.objects.create(
            sport=self.sport, home_team='Home Team', away_team='Away Team',
            start_time=self.start_time, status=Match.Status.SCHEDULED,
            odds_home=Decimal('2.00'), odds_draw=Decimal('3.00'), odds_away=Decimal('4.00'),
        )

        bet = place_bet(user, match.id, 'home', Decimal('10'))
        self.assertEqual(bet.odds, Decimal('2.00'))

        # Admin changes this match's adjustment after the bet was placed.
        match.odds_adjustment = Decimal('5.00')
        match.save(update_fields=['odds_adjustment'])
        with patch('apps.sports.realtime.get_odds_provider', return_value=self._mock_provider()):
            maybe_refresh_sport_odds(self.sport, 20)

        match.refresh_from_db()
        self.assertNotEqual(match.odds_home, Decimal('2.00'))  # the match's odds did move...
        bet.refresh_from_db()
        self.assertEqual(bet.odds, Decimal('2.00'))  # ...but the already-placed bet's odds did not.


class LaySpreadPricingTests(TestCase):
    def test_lay_price_adds_spread_to_back_odds(self):
        self.assertEqual(compute_lay_price(Decimal('2.00'), Decimal('0.10')), Decimal('2.10'))

    def test_lay_price_always_strictly_greater_than_back_odds_even_with_floor_spread(self):
        lay = compute_lay_price(Decimal('1.20'), Decimal('0.01'))
        self.assertGreater(lay, Decimal('1.20'))

    def test_zero_or_negative_spread_input_still_clamped_by_floor(self):
        # Calls compute_lay_price directly with an out-of-contract spread
        # (the model validators should already reject these, but this
        # proves the real, unconditional code-level guarantee holds even
        # if a caller somehow bypasses them).
        back = Decimal('3.00')
        lay_zero = compute_lay_price(back, Decimal('0'))
        lay_negative = compute_lay_price(back, Decimal('-5.00'))
        self.assertGreater(lay_zero, back)
        self.assertGreater(lay_negative, back)
        self.assertEqual(lay_zero, back + Decimal('0.01'))
        self.assertEqual(lay_negative, back + Decimal('0.01'))

    def test_none_back_odds_returns_none(self):
        self.assertIsNone(compute_lay_price(None, Decimal('0.10')))

    def test_resolve_effective_lay_spread_prefers_match_override(self):
        self.assertEqual(
            resolve_effective_lay_spread(Decimal('0.50'), Decimal('0.10')), Decimal('0.50'),
        )

    def test_resolve_effective_lay_spread_falls_back_to_default(self):
        self.assertEqual(
            resolve_effective_lay_spread(None, Decimal('0.10')), Decimal('0.10'),
        )


class PerUserOddsOverrideTests(TestCase):
    def setUp(self):
        self.sport = Sport.objects.create(name='PerUserOddsSport', slug='per-user-odds-sport')
        self.match = Match.objects.create(
            sport=self.sport, home_team='H', away_team='A',
            start_time=timezone.now() + timezone.timedelta(hours=1), status=Match.Status.SCHEDULED,
            odds_home=Decimal('2.00'), odds_draw=Decimal('3.00'), odds_away=Decimal('4.00'),
        )
        self.other_match = Match.objects.create(
            sport=self.sport, home_team='H2', away_team='A2',
            start_time=timezone.now() + timezone.timedelta(hours=1), status=Match.Status.SCHEDULED,
            odds_home=Decimal('5.00'), odds_draw=Decimal('6.00'), odds_away=Decimal('7.00'),
        )
        self.user = User.objects.create_user(email='oddsoverride@example.com', password='testpass123')

    def test_no_override_anywhere_returns_zero(self):
        self.assertEqual(resolve_user_extra_adjustment(self.user, self.match), Decimal('0'))

    def test_none_user_returns_zero(self):
        self.assertEqual(resolve_user_extra_adjustment(None, self.match), Decimal('0'))

    def test_user_global_override_applies_to_any_match(self):
        self.user.odds_adjustment_override = Decimal('-0.50')
        self.user.save(update_fields=['odds_adjustment_override'])
        self.assertEqual(resolve_user_extra_adjustment(self.user, self.match), Decimal('-0.50'))
        self.assertEqual(resolve_user_extra_adjustment(self.user, self.other_match), Decimal('-0.50'))

    def test_user_match_specific_override_beats_user_global_override(self):
        self.user.odds_adjustment_override = Decimal('-0.50')
        self.user.save(update_fields=['odds_adjustment_override'])
        UserMatchOddsOverride.objects.create(user=self.user, match=self.match, adjustment=Decimal('2.00'))

        self.assertEqual(resolve_user_extra_adjustment(self.user, self.match), Decimal('2.00'))
        # The other match isn't covered by the specific override - falls
        # back to this user's global override.
        self.assertEqual(resolve_user_extra_adjustment(self.user, self.other_match), Decimal('-0.50'))

    def test_get_effective_odds_for_user_with_no_override_returns_match_odds_unchanged(self):
        odds = get_effective_odds_for_user(self.match, self.user)
        self.assertEqual(odds, (Decimal('2.00'), Decimal('3.00'), Decimal('4.00')))

    def test_get_effective_odds_for_user_applies_override(self):
        self.user.odds_adjustment_override = Decimal('0.20')
        self.user.save(update_fields=['odds_adjustment_override'])
        odds = get_effective_odds_for_user(self.match, self.user)
        self.assertEqual(odds, (Decimal('2.20'), Decimal('3.20'), Decimal('4.20')))

    def test_get_effective_odds_for_user_still_floor_clamped(self):
        UserMatchOddsOverride.objects.create(user=self.user, match=self.match, adjustment=Decimal('-10.00'))
        odds_home, odds_draw, odds_away = get_effective_odds_for_user(self.match, self.user)
        # 2.00 - 10.00 would be negative without the floor - proves the
        # same ODDS_FLOOR guarantee from apply_odds_adjustment still holds
        # for the per-user layer, since it reuses that function unchanged.
        self.assertEqual(odds_home, Decimal('1.01'))

    def test_get_effective_odds_for_user_never_touches_the_shared_match_row(self):
        UserMatchOddsOverride.objects.create(user=self.user, match=self.match, adjustment=Decimal('1.00'))
        get_effective_odds_for_user(self.match, self.user)
        self.match.refresh_from_db()
        # The shared, public price every other user sees must be completely
        # untouched by a per-user override.
        self.assertEqual(self.match.odds_home, Decimal('2.00'))

    def test_get_effective_odds_for_anonymous_user_returns_match_odds_unchanged(self):
        anonymous = AnonymousUser()
        odds = get_effective_odds_for_user(self.match, anonymous)
        self.assertEqual(odds, (Decimal('2.00'), Decimal('3.00'), Decimal('4.00')))


class OddsAdjustmentAdminResetActionTests(TestCase):
    def _admin_request(self):
        # message_user() (called by every reset action) needs a request
        # with a real messages backend attached - RequestFactory alone
        # doesn't wire one up, so it's attached by hand here, the standard
        # way to unit-test a Django admin action outside of a real view.
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        request = RequestFactory().get('/admin/')
        request.session = {}
        request._messages = FallbackStorage(request)
        return request

    def setUp(self):
        self.sport = Sport.objects.create(name='ResetActionSport', slug='reset-action-sport')
        self.match = Match.objects.create(
            sport=self.sport, home_team='H', away_team='A',
            start_time=timezone.now() + timezone.timedelta(hours=1), status=Match.Status.SCHEDULED,
            odds_home=Decimal('2.00'), odds_draw=Decimal('3.00'), odds_away=Decimal('4.00'),
            odds_adjustment=Decimal('0.50'),
        )
        self.user = User.objects.create_user(
            email='resetaction@example.com', password='testpass123',
            odds_adjustment_override=Decimal('1.00'),
        )
        OddsAdjustmentConfig.objects.filter(pk=1).delete()
        self.config = OddsAdjustmentConfig.objects.create(pk=1, default_adjustment=Decimal('0.30'))

    def test_match_reset_action_clears_override(self):
        from django.contrib import admin as django_admin
        from .admin import MatchAdmin

        admin_instance = MatchAdmin(Match, django_admin.site)
        admin_instance.reset_odds_adjustment_to_default(self._admin_request(), Match.objects.filter(pk=self.match.pk))
        self.match.refresh_from_db()
        self.assertIsNone(self.match.odds_adjustment)

    def test_user_reset_action_clears_override(self):
        from django.contrib import admin as django_admin
        from apps.accounts.admin import CustomUserAdmin

        admin_instance = CustomUserAdmin(User, django_admin.site)
        admin_instance.reset_odds_adjustment_override(self._admin_request(), User.objects.filter(pk=self.user.pk))
        self.user.refresh_from_db()
        self.assertIsNone(self.user.odds_adjustment_override)

    def test_global_config_reset_action_clears_to_zero(self):
        from django.contrib import admin as django_admin
        from .admin import OddsAdjustmentConfigAdmin

        admin_instance = OddsAdjustmentConfigAdmin(OddsAdjustmentConfig, django_admin.site)
        admin_instance.reset_to_default(self._admin_request(), OddsAdjustmentConfig.objects.filter(pk=self.config.pk))
        self.config.refresh_from_db()
        self.assertEqual(self.config.default_adjustment, Decimal('0'))


class PricingOverrideModelTests(TestCase):
    def setUp(self):
        self.sport = Sport.objects.create(name='PricingOverrideSport', slug='pricing-override-sport')
        self.match = Match.objects.create(
            sport=self.sport, home_team='H', away_team='A',
            start_time=timezone.now() + timezone.timedelta(hours=1), status=Match.Status.SCHEDULED,
            odds_home=Decimal('2.00'), odds_draw=Decimal('3.00'), odds_away=Decimal('4.00'),
        )
        self.user = User.objects.create_user(email='pricingoverride@example.com', password='testpass123')

    def test_both_null_rejected_by_clean(self):
        override = PricingOverride(back_mode=BackMode.CUSTOM, back_custom_value_home=Decimal('3.00'))
        with self.assertRaises(ValidationError):
            override.full_clean()

    def test_match_only_row_is_capped_at_one(self):
        PricingOverride.objects.create(match=self.match, back_mode=BackMode.CUSTOM, back_custom_value_home=Decimal('3.00'))
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PricingOverride.objects.create(match=self.match, back_mode=BackMode.CUSTOM, back_custom_value_home=Decimal('4.00'))

    def test_user_only_row_is_capped_at_one(self):
        PricingOverride.objects.create(user=self.user, back_mode=BackMode.CUSTOM, back_custom_value_home=Decimal('3.00'))
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PricingOverride.objects.create(user=self.user, back_mode=BackMode.CUSTOM, back_custom_value_home=Decimal('4.00'))

    def test_user_and_match_can_each_have_their_own_row_simultaneously(self):
        # Confirms the two conditional constraints don't collide with
        # each other - a match-only row and a user-only row can coexist.
        PricingOverride.objects.create(match=self.match, back_mode=BackMode.CUSTOM, back_custom_value_home=Decimal('3.00'))
        PricingOverride.objects.create(user=self.user, back_mode=BackMode.CUSTOM, back_custom_value_home=Decimal('5.00'))
        self.assertEqual(PricingOverride.objects.count(), 2)


class PricingModeResolutionTests(TestCase):
    def setUp(self):
        HouseLiquidityConfig.objects.filter(pk=1).delete()
        self.config = HouseLiquidityConfig.objects.create(pk=1, default_lay_spread=Decimal('0.10'))

        self.sport = Sport.objects.create(name='PricingModeSport', slug='pricing-mode-sport')
        self.match = Match.objects.create(
            sport=self.sport, home_team='H', away_team='A',
            start_time=timezone.now() + timezone.timedelta(hours=1), status=Match.Status.SCHEDULED,
            odds_home=Decimal('2.00'), odds_draw=Decimal('3.00'), odds_away=Decimal('4.00'),
        )
        self.user = User.objects.create_user(email='pricingmode@example.com', password='testpass123')

    def test_resolve_match_pricing_mode_falls_back_to_global_default(self):
        mode = resolve_match_pricing_mode(self.match)
        self.assertEqual(mode.back_mode, BackMode.API)
        self.assertEqual(mode.lay_mode, LayMode.API_LAY)

    def test_resolve_match_pricing_mode_prefers_match_row(self):
        PricingOverride.objects.create(
            match=self.match, back_mode=BackMode.CUSTOM, back_custom_value_home=Decimal('3.15'),
        )
        mode = resolve_match_pricing_mode(self.match)
        self.assertEqual(mode.back_mode, BackMode.CUSTOM)
        self.assertEqual(mode.back_custom_value_home, Decimal('3.15'))

    def test_resolve_user_pricing_mode_none_when_no_override(self):
        self.assertIsNone(resolve_user_pricing_mode(self.user, self.match))

    def test_resolve_user_pricing_mode_prefers_user_match_specific_over_user_only(self):
        PricingOverride.objects.create(user=self.user, back_mode=BackMode.CUSTOM, back_custom_value_home=Decimal('9.00'))
        PricingOverride.objects.create(
            user=self.user, match=self.match, back_mode=BackMode.CUSTOM, back_custom_value_home=Decimal('1.50'),
        )
        mode = resolve_user_pricing_mode(self.user, self.match)
        self.assertEqual(mode.back_custom_value_home, Decimal('1.50'))

    def test_resolve_user_pricing_mode_none_for_anonymous(self):
        self.assertIsNone(resolve_user_pricing_mode(AnonymousUser(), self.match))

    def _mode(self, **overrides):
        from .pricing import ResolvedPricingMode
        defaults = dict(
            back_mode=BackMode.API,
            back_custom_value_home=None, back_custom_value_draw=None, back_custom_value_away=None,
            lay_mode=LayMode.API_LAY, lay_relative_delta=None,
            lay_custom_value_home=None, lay_custom_value_draw=None, lay_custom_value_away=None,
        )
        defaults.update(overrides)
        return ResolvedPricingMode(**defaults)

    def test_compute_back_and_lay_custom_back_api_lay(self):
        mode = self._mode(back_mode=BackMode.CUSTOM, back_custom_value_home=Decimal('3.15'))
        back, lay = compute_back_and_lay(Decimal('2.00'), mode, Decimal('0.10'), 'home')
        self.assertEqual(back, Decimal('3.15'))
        self.assertEqual(lay, Decimal('3.25'))  # custom back (3.15) + house spread (0.10)

    def test_compute_back_and_lay_custom_back_is_per_selection(self):
        mode = self._mode(
            back_mode=BackMode.CUSTOM, back_custom_value_home=Decimal('3.15'),
            back_custom_value_draw=Decimal('5.00'), back_custom_value_away=Decimal('7.00'),
        )
        back_home, _ = compute_back_and_lay(Decimal('2.00'), mode, Decimal('0.10'), 'home')
        back_draw, _ = compute_back_and_lay(Decimal('3.00'), mode, Decimal('0.10'), 'draw')
        back_away, _ = compute_back_and_lay(Decimal('4.00'), mode, Decimal('0.10'), 'away')
        # Each selection gets its OWN custom value - the real bug caught
        # live before this redesign was all three showing the same number.
        self.assertEqual(back_home, Decimal('3.15'))
        self.assertEqual(back_draw, Decimal('5.00'))
        self.assertEqual(back_away, Decimal('7.00'))

    def test_compute_back_and_lay_relative_lay_positive(self):
        mode = self._mode(lay_mode=LayMode.RELATIVE, lay_relative_delta=Decimal('0.30'))
        back, lay = compute_back_and_lay(Decimal('2.20'), mode, Decimal('0.10'), 'home')
        self.assertEqual(back, Decimal('2.20'))
        self.assertEqual(lay, Decimal('2.50'))

    def test_compute_back_and_lay_relative_lay_negative_still_floor_clamped(self):
        # The client's own example: back=3.00, relative delta=-0.60 would
        # naively give lay=2.40 - fine there since it's still > back? No -
        # 2.40 < 3.00, which IS the arbitrage condition. The universal
        # floor-clamp must catch this regardless of what the admin enters.
        mode = self._mode(lay_mode=LayMode.RELATIVE, lay_relative_delta=Decimal('-0.60'))
        back, lay = compute_back_and_lay(Decimal('3.00'), mode, Decimal('0.10'), 'home')
        self.assertEqual(back, Decimal('3.00'))
        self.assertGreater(lay, back)
        self.assertEqual(lay, Decimal('3.01'))  # floor-clamped to back + LAY_SPREAD_FLOOR

    def test_compute_back_and_lay_custom_lay_below_back_is_floor_clamped(self):
        mode = self._mode(lay_mode=LayMode.CUSTOM, lay_custom_value_home=Decimal('1.50'))
        back, lay = compute_back_and_lay(Decimal('4.00'), mode, Decimal('0.10'), 'home')
        self.assertEqual(back, Decimal('4.00'))
        self.assertEqual(lay, Decimal('4.01'))  # 1.50 would be below back - clamped

    def test_compute_back_and_lay_custom_lay_above_back_used_as_is(self):
        mode = self._mode(lay_mode=LayMode.CUSTOM, lay_custom_value_home=Decimal('5.00'))
        back, lay = compute_back_and_lay(Decimal('4.00'), mode, Decimal('0.10'), 'home')
        self.assertEqual(lay, Decimal('5.00'))  # already above back - no clamping needed

    def test_compute_back_and_lay_custom_lay_is_per_selection(self):
        mode = self._mode(
            lay_mode=LayMode.CUSTOM, lay_custom_value_home=Decimal('5.00'),
            lay_custom_value_draw=Decimal('6.00'), lay_custom_value_away=Decimal('7.00'),
        )
        _, lay_home = compute_back_and_lay(Decimal('2.00'), mode, Decimal('0.10'), 'home')
        _, lay_draw = compute_back_and_lay(Decimal('3.00'), mode, Decimal('0.10'), 'draw')
        _, lay_away = compute_back_and_lay(Decimal('4.00'), mode, Decimal('0.10'), 'away')
        self.assertEqual((lay_home, lay_draw, lay_away), (Decimal('5.00'), Decimal('6.00'), Decimal('7.00')))

    def test_compute_back_and_lay_none_back_passes_through(self):
        mode = self._mode()
        self.assertEqual(compute_back_and_lay(None, mode, Decimal('0.10'), 'home'), (None, None))

    def test_get_effective_odds_for_user_uses_users_own_custom_back_override_per_selection(self):
        PricingOverride.objects.create(
            user=self.user, match=self.match, back_mode=BackMode.CUSTOM,
            back_custom_value_home=Decimal('1.50'), back_custom_value_draw=Decimal('2.50'),
            back_custom_value_away=Decimal('3.50'),
        )
        odds_home, odds_draw, odds_away = get_effective_odds_for_user(self.match, self.user)
        self.assertEqual(odds_home, Decimal('1.50'))
        self.assertEqual(odds_draw, Decimal('2.50'))
        self.assertEqual(odds_away, Decimal('3.50'))

    def test_get_effective_odds_for_user_unaffected_when_no_user_override(self):
        PricingOverride.objects.create(match=self.match, back_mode=BackMode.CUSTOM, back_custom_value_home=Decimal('9.99'))
        # A match-level override must NEVER leak into the per-user
        # sportsbook resolution used for bet-charging - only a user-level
        # (or user+match) row can do that. This proves the two-stage split
        # (match/global feeds the shared price; user/user-match is a
        # display/charge override layered on top of THAT, and only ever
        # triggered by the presence of a user-scoped row) holds.
        odds_home, _, _ = get_effective_odds_for_user(self.match, self.user)
        self.assertEqual(odds_home, self.match.odds_home)

    def test_get_effective_lay_reference_for_user_matches_users_mode(self):
        PricingOverride.objects.create(
            user=self.user, match=self.match, back_mode=BackMode.API,
            lay_mode=LayMode.CUSTOM, lay_custom_value_home=Decimal('9.00'),
            lay_custom_value_draw=Decimal('9.50'), lay_custom_value_away=Decimal('10.00'),
        )
        lay_home, lay_draw, lay_away = get_effective_lay_reference_for_user(self.match, self.user)
        self.assertEqual(lay_home, Decimal('9.00'))
        self.assertEqual(lay_draw, Decimal('9.50'))
        self.assertEqual(lay_away, Decimal('10.00'))

    def test_get_effective_lay_reference_for_user_falls_back_to_match_mode(self):
        PricingOverride.objects.create(match=self.match, lay_mode=LayMode.RELATIVE, lay_relative_delta=Decimal('0.40'))
        lay_home, _, _ = get_effective_lay_reference_for_user(self.match, self.user)
        self.assertEqual(lay_home, Decimal('2.40'))  # 2.00 + 0.40, no user override present


class RefreshModeResolutionTests(TestCase):
    def setUp(self):
        RealtimeOddsConfig.objects.filter(pk=1).delete()
        self.config = RealtimeOddsConfig.objects.create(pk=1, default_refresh_mode=RefreshMode.VIEWER_GATED)
        self.sport = Sport.objects.create(name='RefreshModeResSport', slug='refresh-mode-res-sport')
        self.match = Match.objects.create(
            sport=self.sport, home_team='H', away_team='A',
            start_time=timezone.now() + timezone.timedelta(hours=1), status=Match.Status.SCHEDULED,
        )

    def test_falls_back_to_global_default_when_no_match_override(self):
        self.assertEqual(resolve_effective_refresh_mode(self.match, self.config), RefreshMode.VIEWER_GATED)

    def test_match_override_beats_global_default(self):
        self.match.refresh_mode_override = RefreshMode.STOPPED
        self.match.save(update_fields=['refresh_mode_override'])
        self.assertEqual(resolve_effective_refresh_mode(self.match, self.config), RefreshMode.STOPPED)

    def test_global_default_can_itself_be_changed(self):
        self.config.default_refresh_mode = RefreshMode.ALWAYS
        self.config.save()
        self.assertEqual(resolve_effective_refresh_mode(self.match, self.config), RefreshMode.ALWAYS)
