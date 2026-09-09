"""
Waija casino provider - the active provider (CASINO_PROVIDER=waija). Kept in
its own file, separate from providers.py and providers_softapi.py, so each
provider's own details never leak into or clutter the others - the same
provider-flexibility discipline used for apps.sports/apps.payments, applied
at file-granularity now that there are two real casino providers.
"""
import hashlib
import hmac
import json
import urllib.error
import urllib.request
import zlib
from decimal import Decimal

from .providers import BaseCasinoProvider


def _env(name: str, default: str = '') -> str:
    import os
    return os.getenv(name, default)


def _crc32_id(text: str) -> int:
    """Deterministic positive integer key for a Waija category name (which has no numeric brand id of its own)."""
    return zlib.crc32(text.encode('utf-8')) & 0x7FFFFFFF


class WaijaProvider(BaseCasinoProvider):
    """
    Real implementation for Waija (branded front for the underlying
    SlotsGateway product, per its own GitHub package name
    'slotsgateway/slotsgateway-php-client' and PHP client docs - same
    "the docs use a different brand name than the actual engine" pattern
    already seen once with SoftAPI/igamingapis in this project).

    Requires WAIJA_API_LOGIN, WAIJA_API_PASSWORD, WAIJA_API_URL (from the
    account's own Settings page - no public default), WAIJA_SALTKEY (for
    verifying inbound wallet callbacks), and WAIJA_PLAYER_SECRET (used
    only locally to derive a stable per-platform-user Waija sub-account
    username/password - never sent anywhere but Waija's own API, and never
    needs to be persisted since it's re-derived identically every call).

    Unlike SoftAPI (one numeric user_id, one launch call), Waija requires
    a distinct player sub-account to be created once per user
    (createPlayer, idempotent - it internally redirects to playerExists if
    the account already exists) before every getGame call, and getGame
    must be called with that exact same username/password every time.
    """

    name = 'waija'
    requires_upfront_balance = False

    def __init__(self):
        self.api_login = _env('WAIJA_API_LOGIN')
        self.api_password = _env('WAIJA_API_PASSWORD')
        self.base_url = _env('WAIJA_API_URL').rstrip('/')
        self.saltkey = _env('WAIJA_SALTKEY')
        self.player_secret = _env('WAIJA_PLAYER_SECRET')

    def _require_credentials(self):
        if not self.api_login or not self.api_password or not self.base_url:
            raise RuntimeError('WAIJA_API_LOGIN / WAIJA_API_PASSWORD / WAIJA_API_URL are not configured.')

    def _call(self, method: str, extra: dict) -> dict:
        self._require_credentials()
        payload = {'api_login': self.api_login, 'api_password': self.api_password, 'method': method}
        payload.update(extra)
        request = urllib.request.Request(
            self.base_url,
            data=json.dumps(payload).encode('utf-8'),
            # Cloudflare (fronting this API, like ParlayAPI) blocks requests
            # carrying Python's default urllib User-Agent with a 403 (error
            # code 1010) - confirmed by direct testing. A normal-looking
            # User-Agent avoids that; this has nothing to do with
            # authentication (api_login/api_password in the body).
            headers={
                'Content-Type': 'application/json', 'Accept': 'application/json',
                'User-Agent': 'Mozilla/5.0 (compatible; QuickPayBet/1.0)',
            },
            method='POST',
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode('utf-8')
            raise RuntimeError(f'Waija HTTP {exc.code}: {body}')
        if int(result.get('error', -1)) != 0:
            raise RuntimeError(f"Waija {method} error: {result.get('message', result)}")
        return result

    def _player_credentials(self, user_id) -> tuple[str, str]:
        """
        Deterministically derives this platform user's Waija sub-account
        username/password from WAIJA_PLAYER_SECRET - always the same
        output for the same user_id, so nothing needs to be stored in our
        own database; createPlayer is safe to call every launch (it just
        redirects to playerExists once the account exists at Waija).
        """
        if not self.player_secret:
            raise RuntimeError('WAIJA_PLAYER_SECRET is not configured.')
        username = f'qpb{user_id}'
        password = hmac.new(
            self.player_secret.encode('utf-8'), f'waija-user-{user_id}'.encode('utf-8'), hashlib.sha256,
        ).hexdigest()[:32]
        return username, password

    def _normalize_game(self, entry: dict) -> dict:
        return {
            'game_id': entry.get('id'),
            'game_uid': entry.get('id_hash'),
            'brand_id': _crc32_id(entry.get('category', 'other')),
            'name': entry.get('name', ''),
            'category': entry.get('type', entry.get('category', '')),
            'logo': entry.get('image', ''),
        }

    def fetch_providers(self) -> list[dict]:
        # Waija has no separate providers/brands endpoint - a game's
        # `category` field (e.g. "spinomenal", "bgaming") is the closest
        # equivalent to a studio/brand, so brands are derived from the
        # distinct categories seen in the games list.
        games = self._call('getGameList', {'show_additional': True, 'show_systems': 0, 'list_type': 1, 'currency': 'USD'})
        seen = {}
        for entry in games.get('response', []):
            category = entry.get('category', 'other')
            brand_id = _crc32_id(category)
            if brand_id not in seen:
                seen[brand_id] = {'brand_id': brand_id, 'name': category, 'logo': '', 'game_count': 0, 'currency_supported': True}
            seen[brand_id]['game_count'] += 1
        return list(seen.values())

    def fetch_games(self, brand_id=None) -> list[dict]:
        games = self._call('getGameList', {'show_additional': True, 'show_systems': 0, 'list_type': 1, 'currency': 'USD'})
        normalized = [self._normalize_game(entry) for entry in games.get('response', [])]
        if brand_id is not None:
            normalized = [g for g in normalized if g['brand_id'] == brand_id]
        return normalized

    def launch_game(self, *, user_id, balance: Decimal, game_uid: str, currency_code: str,
                     language: str, return_url: str, callback_url: str) -> dict:
        username, password = self._player_credentials(user_id)
        self._call('createPlayer', {
            'user_username': username, 'user_password': password, 'currency': currency_code,
        })
        result = self._call('getGame', {
            'lang': language,
            'user_username': username,
            'user_password': password,
            'gameid': game_uid,
            'homeurl': return_url,
            'cashierurl': return_url,
            'play_for_fun': 0,
            'currency': currency_code,
        })
        return {'url': result['response']}

    def fetch_demo_url(self, game_uid: str, return_url: str = '') -> str:
        # Unlike getGame, Waija's own docs example for getGameDemo shows
        # homeurl/cashierurl as required, non-empty fields too - confirmed
        # live: an empty string is rejected with a 422 validation error.
        url = return_url or 'https://example.com/'
        result = self._call('getGameDemo', {
            'lang': 'en', 'gameid': game_uid, 'homeurl': url, 'cashierurl': url, 'currency': 'USD',
        })
        return result['response']

    def fetch_ggr_balance(self) -> Decimal:
        raise RuntimeError('Waija has no GGR wallet concept exposed via this API (billing is handled in their backoffice).')

    def decrypt_callback(self, raw_body: bytes) -> dict:
        raise NotImplementedError('Waija sends wallet callbacks as signed GET requests, not POST - see apps.casino.views.WaijaWalletCallbackView.')

    def verify_callback_signature(self, key: str, timestamp: str) -> bool:
        """md5(timestamp + saltkey), per apps.casino.views.WaijaWalletCallbackView - a 30-second freshness window is enforced by the caller."""
        if not self.saltkey:
            return False
        expected = hashlib.md5(f'{timestamp}{self.saltkey}'.encode('utf-8')).hexdigest()
        return hmac.compare_digest(expected, key)
