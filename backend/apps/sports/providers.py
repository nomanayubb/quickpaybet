import urllib.request
import json
import os


class BaseOddsProvider:
    def __init__(self):
        self.name = 'base'

    def fetch_matches(self) -> list[dict]:
        raise NotImplementedError


class MockOddsProvider(BaseOddsProvider):
    """
    Returns static sample matches for development.
    In production, replace the URL logic with a real provider's API.
    """

    def __init__(self):
        self.name = 'mock'

    def fetch_matches(self) -> list[dict]:
        return [
            {
                'sport_slug': 'football',
                'sport_name': 'Football',
                'tournament_name': 'English Premier League',
                'season': '2025/26',
                'home_team': 'Arsenal',
                'away_team': 'Chelsea',
                'start_time': '2025-07-15T18:00:00Z',
                'odds_home': '2.20',
                'odds_draw': '3.40',
                'odds_away': '3.10',
            },
            {
                'sport_slug': 'football',
                'sport_name': 'Football',
                'tournament_name': 'La Liga',
                'season': '2025/26',
                'home_team': 'Real Madrid',
                'away_team': 'Barcelona',
                'start_time': '2025-07-16T20:00:00Z',
                'odds_home': '2.00',
                'odds_draw': '3.40',
                'odds_away': '3.80',
            },
        ]


class TheyOddsAPIProvider(BaseOddsProvider):
    """
    Example skeleton for a real provider (e.g., The Odds API).
    Requires ODDS_API_KEY / ODDS_API_URL environment variables.
    """
    def __init__(self):
        self.name = 'theoddsapi'
        self.api_key = os.getenv('ODDS_API_KEY', '')
        self.api_url = os.getenv('ODDS_API_URL', 'https://api.the-odds-api.com/v4/sports')

    def fetch_matches(self) -> list[dict]:
        # Real implementation would parse JSON and return normalized dicts.
        # This placeholder raises a clear error until credentials are set.
        if not self.api_key:
            raise ValueError('ODDS_API_KEY is not set. Use MockOddsProvider for development.')
        # You would use urllib here:
        # url = f"{self.api_url}?apiKey={self.api_key}&regions=eu&markets=h2h"
        # with urllib.request.urlopen(url) as response:
        #     data = json.loads(response.read().decode())
        # return normalized_matches(data)
        raise NotImplementedError('Implement provider logic when ready.')


def get_odds_provider() -> BaseOddsProvider:
    provider_name = os.getenv('ODDS_PROVIDER', 'mock').lower()
    if provider_name == 'mock':
        return MockOddsProvider()
    if provider_name == 'theoddsapi':
        return TheyOddsAPIProvider()
    raise NotImplementedError(f'Unknown odds provider: {provider_name}')
