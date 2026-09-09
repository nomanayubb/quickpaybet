"""
SoftAPI casino provider - kept in its own file, separate from providers.py,
specifically so it stays out of the way of the currently-active provider
(Waija) while remaining ready to use if this account ever becomes viable
again (see docs/PROJECT_MASTER_DOCUMENTATION Section 4.23 for why it isn't
active: WM Casino, the only game live on this account, geo-blocks Pakistan,
and igamingapis support went unresponsive once that was raised with them).

Not imported by apps.casino.providers at module load time - only pulled in
by get_casino_provider() if CASINO_PROVIDER=softapi is explicitly set, so an
unconfigured/unused SoftAPI account never adds any runtime cost or risk to
the active Waija integration.
"""
import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal

from cryptography.hazmat.primitives import padding as sym_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .providers import BaseCasinoProvider


def _pkcs7_encrypt_aes_ecb(plaintext: bytes, secret32: str) -> bytes:
    if len(secret32) != 32:
        raise RuntimeError('SOFTAPI_SECRET must be exactly 32 characters.')
    padder = sym_padding.PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(secret32.encode('utf-8')), modes.ECB()).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def _pkcs7_decrypt_aes_ecb(ciphertext: bytes, secret32: str) -> bytes:
    if len(secret32) != 32:
        raise RuntimeError('SOFTAPI_SECRET must be exactly 32 characters.')
    decryptor = Cipher(algorithms.AES(secret32.encode('utf-8')), modes.ECB()).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = sym_padding.PKCS7(algorithms.AES.block_size).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


class SoftAPIProvider(BaseCasinoProvider):
    """
    Real implementation for the SoftAPI casino aggregator (branded
    igamingapis.live, migrating to world-casino-api.com - the old domain
    shut off 2026-09-10, so the default below already points at the new
    one). Requires SOFTAPI_TOKEN and a 32-character SOFTAPI_SECRET.

    Launch payloads are AES-256-ECB + PKCS7 + Base64 encrypted with the
    account secret per the provider's own docs; list endpoints (providers,
    games, demo, ggr-balance) are plain GET + ?token=, no encryption.
    """

    name = 'softapi'

    def __init__(self):
        self.token = os.getenv('SOFTAPI_TOKEN', '')
        self.secret = os.getenv('SOFTAPI_SECRET', '')
        self.base_url = os.getenv('SOFTAPI_API_URL', 'https://world-casino-api.com/api/v1').rstrip('/')

    def _require_credentials(self):
        if not self.token:
            raise RuntimeError('SOFTAPI_TOKEN is not configured.')
        if not self.secret:
            raise RuntimeError('SOFTAPI_SECRET is not configured.')

    def _get(self, path: str, params: dict | None = None):
        self._require_credentials()
        query = dict(params or {})
        query['token'] = self.token
        url = f'{self.base_url}{path}?{urllib.parse.urlencode(query)}'
        request = urllib.request.Request(url, headers={'Accept': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.read().decode('utf-8')
        except urllib.error.HTTPError as exc:
            body = exc.read().decode('utf-8')
            raise RuntimeError(f'SoftAPI HTTP {exc.code}: {body}')

    def _post_json(self, path: str, payload: dict) -> dict:
        self._require_credentials()
        url = f'{self.base_url}{path}'
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json', 'Accept': 'application/json'},
            method='POST',
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode('utf-8')
            raise RuntimeError(f'SoftAPI HTTP {exc.code}: {body}')

    def fetch_providers(self) -> list[dict]:
        raw = json.loads(self._get('/providers', {'currency_supported': 1}))
        if int(raw.get('code', -1)) != 0:
            raise RuntimeError(f"SoftAPI providers error: {raw.get('msg')}")
        result = []
        for entry in raw.get('data', {}).get('providers', []):
            result.append({
                'brand_id': entry.get('brand_id'),
                'name': entry.get('name', ''),
                'logo': entry.get('logo', ''),
                'game_count': entry.get('game_count', 0),
                'currency_supported': entry.get('currency_supported', True),
            })
        return result

    def fetch_games(self, brand_id=None) -> list[dict]:
        params = {'currency_supported': 1}
        if brand_id is not None:
            params['brand_id'] = brand_id
            params['limit'] = 500
        else:
            params['limit'] = 20000
        raw = json.loads(self._get('/games', params))
        if int(raw.get('code', -1)) != 0:
            raise RuntimeError(f"SoftAPI games error: {raw.get('msg')}")
        result = []
        for entry in raw.get('data', {}).get('games', []):
            result.append({
                'game_id': entry.get('game_id'),
                'game_uid': str(entry.get('game_uid')),
                'brand_id': entry.get('brand_id'),
                'name': entry.get('name', ''),
                'category': entry.get('category', ''),
                'logo': entry.get('logo', ''),
                'currency_supported': entry.get('currency_supported', True),
            })
        return result

    def launch_game(self, *, user_id, balance: Decimal, game_uid: str, currency_code: str,
                     language: str, return_url: str, callback_url: str) -> dict:
        import time
        plain_payload = {
            'user_id': user_id,
            'balance': float(balance),
            'game_uid': str(game_uid),
            'token': self.token,
            'timestamp': int(time.time() * 1000),
            'return': return_url,
            'callback': callback_url,
            'currency_code': currency_code,
            'language': language,
        }
        encrypted = base64.b64encode(
            _pkcs7_encrypt_aes_ecb(
                json.dumps(plain_payload, separators=(',', ':')).encode('utf-8'),
                self.secret,
            )
        ).decode('ascii')

        result = self._post_json('', {'token': self.token, 'payload': encrypted})
        if int(result.get('code', -1)) != 0:
            raise RuntimeError(f"SoftAPI launch error: {result.get('msg')}")
        url = (result.get('data') or {}).get('url')
        if not url:
            raise RuntimeError('SoftAPI launch response missing data.url.')
        return {'url': url}

    def fetch_demo_url(self, game_uid: str, return_url: str = '') -> str:
        body = self._get('/demo', {'game_uid': game_uid})
        stripped = body.strip()
        # Demo errors come back as JSON ({"code": ..., "msg": ...}); success
        # is a bare play-URL string (text/plain, no JSON wrapper at all).
        if stripped.startswith('{'):
            try:
                parsed = json.loads(stripped)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict) and 'code' in parsed:
                raise RuntimeError(f"SoftAPI demo error: {parsed.get('msg')}")
        return stripped

    def fetch_ggr_balance(self) -> Decimal:
        raw = json.loads(self._get('/ggr-balance'))
        if int(raw.get('code', -1)) != 0:
            raise RuntimeError(f"SoftAPI GGR balance error: {raw.get('msg')}")
        return Decimal(str(raw.get('data', {}).get('wallet', '0')))

    def decrypt_callback(self, raw_body: bytes) -> dict:
        body = json.loads(raw_body.decode('utf-8'))
        if 'payload' in body:
            # Encrypted notify (account has enc=1 enabled) - the shared
            # secret is what authenticates this callback at all: only a
            # holder of it could have produced bytes that decrypt cleanly.
            plaintext = _pkcs7_decrypt_aes_ecb(base64.b64decode(body['payload']), self.secret)
            return json.loads(plaintext.decode('utf-8'))
        return body
