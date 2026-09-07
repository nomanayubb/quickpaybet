import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.bets.services import place_bet
from apps.wallet.models import Wallet
from apps.wallet.services import deposit_funds

from .models import Match, RealtimeOddsConfig, Sport
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
