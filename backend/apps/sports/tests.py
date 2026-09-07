import json
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from .providers import (
    BaseOddsProvider,
    MockOddsProvider,
    ParlayAPIProvider,
    TheyOddsAPIProvider,
    get_odds_provider,
)


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
