import json
import os
import urllib.request
import urllib.parse


class BaseOddsProvider:
    def __init__(self):
        self.name = 'base'

    def fetch_matches(self, sport_key=None) -> list[dict]:
        raise NotImplementedError


class MockOddsProvider(BaseOddsProvider):
    """
    Returns static sample matches for development.
    In production, replace the URL logic with a real provider's API.
    """

    def __init__(self):
        self.name = 'mock'

    def fetch_matches(self, sport_key=None) -> list[dict]:
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
    Real implementation for The Odds API.
    Requires ODDS_API_KEY and optionally ODDS_SPORT_KEY / ODDS_API_URL.
    fetch_matches returns normalized matches with H2H + Draw odds.
    """

    def __init__(self):
        self.name = 'theoddsapi'
        self.api_key = os.getenv('ODDS_API_KEY', '')
        self.base_url = os.getenv(
            'ODDS_API_URL',
            'https://api.the-odds-api.com/v4',
        ).rstrip('/')
        self.sport_key = os.getenv('ODDS_SPORT_KEY', 'soccer_epl')

    def _raw_get(self, path: str) -> dict | list:
        if not self.api_key:
            raise RuntimeError('ODDS_API_KEY is not configured.')
        url = f'{self.base_url}{path}'
        if '?' in url:
            url += f'&apiKey={urllib.parse.quote(self.api_key)}'
        else:
            url += f'?apiKey={urllib.parse.quote(self.api_key)}'

        request = urllib.request.Request(url)
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode('utf-8'))

    def fetch_matches(self, sport_key=None) -> list[dict]:
        """Fetch scheduled matches / odds for a configured sport key."""

        # Use the passed sport_key if provided, otherwise use the env default
        key = sport_key or self.sport_key

        # Get odds from the real provider
        path = f'/sports/{key}/odds/?regions=eu&markets=h2h,draw'
        raw_data = self._raw_get(path)

        if not isinstance(raw_data, list):
            return []

        normalized = []
        for event_data in raw_data:
            home_team = event_data.get('home_team')
            away_team = event_data.get('away_team')
            start_time = event_data.get('commence_time')
            if not home_team or not away_team or not start_time:
                continue

            odds_home = odds_draw = odds_away = None
            # The Odds API returns multiple books/markets. We use the first H2H market.
            for book in event_data.get('bookmakers', []):
                for market in book.get('markets', []):
                    if market.get('key') != 'h2h':
                        continue
                    # h2h outcomes: "Home", "Draw", "Away"
                    for outcome in market.get('outcomes', []):
                        price = str(outcome.get('price', ''))
                        name = outcome.get('name', '')
                        if 'home' in name.lower():
                            odds_home = price
                        elif 'draw' in name.lower():
                            odds_draw = price
                        elif 'away' in name.lower():
                            odds_away = price
                    # Use first H2H book only
                    break
                if odds_home or odds_draw or odds_away:
                    break

            if not (odds_home and odds_draw and odds_away):
                continue

            normalized.append({
                'sport_slug': key,          # The Odds API key
                'sport_name': event_data.get('sport_title', key),
                'tournament_name': event_data.get('sport_title', ''),
                'season': '',
                'home_team': home_team,
                'away_team': away_team,
                'start_time': start_time,
                'odds_home': odds_home,
                'odds_draw': odds_draw,
                'odds_away': odds_away,
            })

        return normalized


def get_odds_provider() -> BaseOddsProvider:
    provider_name = os.getenv('ODDS_PROVIDER', 'mock').lower()
    if provider_name == 'mock':
        return MockOddsProvider()
    if provider_name == 'theoddsapi':
        return TheyOddsAPIProvider()
    raise NotImplementedError(f'Unknown odds provider: {provider_name}')
