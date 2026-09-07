import json
import os
import urllib.request
import urllib.parse
import urllib.error


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_score_map(score_entries):
    score_map = {}
    for entry in score_entries or []:
        name = entry.get('name')
        score = entry.get('score')
        if name is not None:
            score_map[name] = score
    return score_map


class BaseOddsProvider:
    # Sport keys this provider is known NOT to serve real data for (e.g. a
    # generic umbrella key with no actual odds endpoint). Provider-specific,
    # not universal - each provider states its own, rather than every call
    # site sharing one hardcoded, one-provider's-quirk constant.
    invalid_sport_keys: set[str] = set()

    def __init__(self):
        self.name = 'base'

    def fetch_matches(self, sport_key=None) -> list[dict]:
        raise NotImplementedError

    def fetch_sports(self) -> list[dict]:
        raise NotImplementedError

    def fetch_scores(self, sport_key=None) -> list[dict]:
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

    def fetch_sports(self) -> list[dict]:
        return [
            {
                'key': 'football',
                'title': 'Football',
                'active': True,
                'group': 'Football',
            },
        ]

    def fetch_scores(self, sport_key=None) -> list[dict]:
        return []


class TheyOddsAPIProvider(BaseOddsProvider):
    """
    Real implementation for The Odds API.
    Requires ODDS_API_KEY and optionally ODDS_SPORT_KEY / ODDS_API_URL.
    fetch_matches returns normalized matches with H2H + Draw odds.
    fetch_scores returns normalized results for recently finished matches.
    """

    invalid_sport_keys = {'football'}  # The Odds API has no generic "football" key

    def __init__(self):
        self.name = 'theoddsapi'
        self.api_key = os.getenv('ODDS_API_KEY', '')
        self.base_url = os.getenv(
            'ODDS_API_URL',
            'https://api.the-odds-api.com/v4',
        ).rstrip('/')
        self.sport_key = os.getenv('ODDS_SPORT_KEY', 'soccer_epl')

    def _raw_get(self, path: str):
        if not self.api_key:
            raise RuntimeError('ODDS_API_KEY is not configured.')
        url = f'{self.base_url}{path}'
        if '?' in url:
            url += f'&apiKey={urllib.parse.quote(self.api_key)}'
        else:
            url += f'?apiKey={urllib.parse.quote(self.api_key)}'

        request = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as exc:
            # 404 means an invalid sport key (e.g., "football")
            # 422 means no odds are currently available for this sport.
            # Both should be treated as "no data" rather than a crash.
            if exc.code in (404, 422):
                return []
            raise

    def fetch_matches(self, sport_key=None) -> list[dict]:
        """Fetch scheduled matches / odds for a configured sport key."""

        # Use the passed sport_key if provided, otherwise use the env default
        key = sport_key or self.sport_key

        # Get odds from the real provider.
        # The Odds API only accepts "h2h" as a valid market key.
        # The "h2h" market already returns a "Draw" outcome for sports that support it.
        path = f'/sports/{key}/odds/?regions=eu&markets=h2h&oddsFormat=decimal'
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
            # The Odds API returns multiple bookmakers. We use the first H2H market.
            for book in event_data.get('bookmakers', []):
                for market in book.get('markets', []):
                    if market.get('key') != 'h2h':
                        continue
                    # H2H outcomes are named after the actual teams (e.g.
                    # "Aston Villa", "Nottingham Forest") plus a literal
                    # "Draw" entry when the sport has one - never the literal
                    # words "home"/"away". Match against the event's own
                    # home_team/away_team strings, not a substring check.
                    for outcome in market.get('outcomes', []):
                        price = str(outcome.get('price', ''))
                        name = outcome.get('name', '')
                        if name == home_team:
                            odds_home = price
                        elif name == away_team:
                            odds_away = price
                        elif 'draw' in name.lower():
                            odds_draw = price
                    # Use the first H2H book only
                    break
                if odds_home or odds_draw or odds_away:
                    break

            if not (odds_home and odds_away):
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
                'odds_draw': odds_draw,     # Can be None when no draw outcome
                'odds_away': odds_away,
            })

        return normalized

    def fetch_scores(self, sport_key=None) -> list[dict]:
        """Fetch completed / live scores for a configured sport key.

        The Odds API scores endpoint provides final scores for games
        that have been completed within the last day.
        """
        key = sport_key or self.sport_key
        path = f'/sports/{key}/scores/?daysFrom=1'
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

            score_map = _coerce_score_map(event_data.get('scores'))
            home_score = None
            away_score = None

            if home_team in score_map:
                home_score = _to_int(score_map[home_team])
            if away_team in score_map:
                away_score = _to_int(score_map[away_team])

            if home_score is None and 'Home' in score_map:
                home_score = _to_int(score_map['Home'])
            if away_score is None and 'Away' in score_map:
                away_score = _to_int(score_map['Away'])

            normalized.append({
                'sport_slug': key,
                'sport_name': event_data.get('sport_title', key),
                'home_team': home_team,
                'away_team': away_team,
                'start_time': start_time,
                'completed': event_data.get('completed', False),
                'home_score': home_score,
                'away_score': away_score,
            })

        return normalized

    def fetch_sports(self) -> list[dict]:
        """Fetch list of supported sports from The Odds API."""
        raw = self._raw_get('/sports/')
        if not isinstance(raw, list):
            return []

        result = []
        for entry in raw:
            result.append({
                'key': entry.get('key', ''),
                'title': entry.get('title', ''),
                'active': entry.get('active', True),
                'group': entry.get('group', ''),
            })
        return result


class ParlayAPIProvider(BaseOddsProvider):
    """
    Real implementation for ParlayAPI (parlay-api.com) - a drop-in-compatible
    replacement for The Odds API's v4 response shape, confirmed by direct
    testing against the live API (not just its docs, which were wrong about
    cricket coverage). Requires PARLAY_API_KEY and optionally
    PARLAY_API_URL / PARLAY_BOOKMAKER.

    Betfair is NOT a data source on this API at all (confirmed against its
    full /v1/bookmakers roster) - PARLAY_BOOKMAKER defaults to "pinnacle"
    instead. Pinnacle doesn't price every sport/match, so fetch_matches
    prefers it but falls back to whichever other bookmaker the API does
    have a price from, rather than showing no odds at all.
    """

    def __init__(self):
        self.name = 'parlayapi'
        self.api_key = os.getenv('PARLAY_API_KEY', '')
        self.base_url = os.getenv('PARLAY_API_URL', 'https://parlay-api.com/v1').rstrip('/')
        self.preferred_bookmaker = os.getenv('PARLAY_BOOKMAKER', 'pinnacle')

    def _raw_get(self, path: str):
        if not self.api_key:
            raise RuntimeError('PARLAY_API_KEY is not configured.')
        url = f'{self.base_url}{path}'
        # Cloudflare (fronting this API) blocks requests carrying Python's
        # default urllib User-Agent with a 403 (error code 1010) - confirmed
        # by direct testing. A normal-looking User-Agent avoids that; this
        # has nothing to do with authentication, which is the header below.
        request = urllib.request.Request(url, headers={
            'X-API-Key': self.api_key,
            'User-Agent': 'Mozilla/5.0 (compatible; QuickPayBet/1.0)',
        })
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as exc:
            # 404/400 covers an invalid or not-yet-populated sport key;
            # treat as "no data" rather than crashing the sync command.
            if exc.code in (400, 404, 422):
                return []
            raise

    def _pick_bookmaker(self, bookmakers: list[dict]) -> dict | None:
        if not bookmakers:
            return None
        for book in bookmakers:
            if book.get('key') == self.preferred_bookmaker:
                return book
        return bookmakers[0]

    def fetch_matches(self, sport_key=None) -> list[dict]:
        """Fetch scheduled matches / odds for a given sport key."""
        if not sport_key:
            return []

        path = f'/sports/{sport_key}/odds?regions=eu&markets=h2h&oddsFormat=decimal'
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

            book = self._pick_bookmaker(event_data.get('bookmakers', []))
            if not book:
                continue

            odds_home = odds_draw = odds_away = None
            for market in book.get('markets', []):
                if market.get('key') != 'h2h':
                    continue
                # Outcomes are named after the actual teams (e.g. "Aston
                # Villa") plus a literal "Draw" entry - never the literal
                # words "home"/"away" - match against the event's own
                # home_team/away_team strings.
                for outcome in market.get('outcomes', []):
                    price = str(outcome.get('price', ''))
                    name = outcome.get('name', '')
                    if name == home_team:
                        odds_home = price
                    elif name == away_team:
                        odds_away = price
                    elif 'draw' in name.lower():
                        odds_draw = price
                break

            if not (odds_home and odds_away):
                continue

            normalized.append({
                'sport_slug': sport_key,
                'sport_name': event_data.get('sport_title', sport_key),
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

    def fetch_scores(self, sport_key=None) -> list[dict]:
        """Fetch completed scores for a given sport key."""
        if not sport_key:
            return []

        path = f'/sports/{sport_key}/scores?daysFrom=3'
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

            score_map = _coerce_score_map(event_data.get('scores'))
            home_score = _to_int(score_map.get(home_team))
            away_score = _to_int(score_map.get(away_team))

            normalized.append({
                'sport_slug': sport_key,
                'sport_name': event_data.get('sport_title', sport_key),
                'home_team': home_team,
                'away_team': away_team,
                'start_time': start_time,
                'completed': event_data.get('completed', False),
                'home_score': home_score,
                'away_score': away_score,
            })

        return normalized

    def fetch_sports(self) -> list[dict]:
        """Fetch the full list of sports/leagues from ParlayAPI (free endpoint - 0 credits)."""
        raw = self._raw_get('/sports')
        if not isinstance(raw, list):
            return []

        result = []
        for entry in raw:
            result.append({
                'key': entry.get('key', ''),
                'title': entry.get('title', ''),
                'active': entry.get('active', True),
                'group': entry.get('group', ''),
            })
        return result


def get_odds_provider() -> BaseOddsProvider:
    provider_name = os.getenv('ODDS_PROVIDER', 'mock').lower()
    if provider_name == 'mock':
        return MockOddsProvider()
    if provider_name == 'theoddsapi':
        return TheyOddsAPIProvider()
    if provider_name == 'parlayapi':
        return ParlayAPIProvider()
    raise NotImplementedError(f'Unknown odds provider: {provider_name}')
