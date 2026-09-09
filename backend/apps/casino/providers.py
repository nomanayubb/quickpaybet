import json
import os
from decimal import Decimal


class BaseCasinoProvider:
    """
    Generic casino/iGaming aggregator interface - every method returns
    normalized dicts, never a provider-native shape, same discipline as
    apps.sports.providers.BaseOddsProvider / apps.payments.providers.
    BasePaymentProvider. No caller outside a provider's own file may know
    anything provider-specific (field names, encryption scheme, base URL) -
    see the project's standing provider-flexibility rule. Each concrete
    provider lives in its own dedicated file (providers_softapi.py,
    providers_waija.py) rather than all together here, so the currently
    active provider (Waija) never shares a file with an inactive one
    (SoftAPI) kept around only in case it becomes usable again later.
    """
    name = 'base'

    # True for a provider whose launch call needs a starting balance handed
    # over up front (SoftAPI: it holds that balance during play, and only
    # tells us the result via settle callbacks). False for a genuine
    # seamless-wallet provider (Waija: no balance is ever handed over -
    # every bet/win is its own live callback against the real, live
    # wallet) - apps.casino.services.launch_game branches on this so a
    # seamless provider is never double-charged (once at launch, again per
    # spin).
    requires_upfront_balance = True

    def fetch_providers(self) -> list[dict]:
        raise NotImplementedError

    def fetch_games(self, brand_id=None) -> list[dict]:
        raise NotImplementedError

    def launch_game(self, *, user_id, balance: Decimal, game_uid: str, currency_code: str,
                     language: str, return_url: str, callback_url: str) -> dict:
        """Returns {'url': str}."""
        raise NotImplementedError

    def fetch_demo_url(self, game_uid: str, return_url: str = '') -> str:
        raise NotImplementedError

    def fetch_ggr_balance(self) -> Decimal:
        raise NotImplementedError

    def decrypt_callback(self, raw_body: bytes) -> dict:
        """Parses/decrypts an inbound settle-notify body into a plain dict."""
        raise NotImplementedError

    def verify_callback_signature(self, key: str, timestamp: str) -> bool:
        """For a true seamless-wallet provider (e.g. Waija) whose wallet callbacks are signed separately from decrypt_callback's model."""
        raise NotImplementedError


class MockCasinoProvider(BaseCasinoProvider):
    name = 'mock'

    def fetch_providers(self) -> list[dict]:
        return [{'brand_id': 1, 'name': 'Mock Slots', 'logo': '', 'game_count': 1, 'currency_supported': True}]

    def fetch_games(self, brand_id=None) -> list[dict]:
        return [{
            'game_id': 1, 'game_uid': '1', 'brand_id': 1, 'name': 'Mock Aviator',
            'category': 'slots', 'logo': '', 'currency_supported': True,
        }]

    def launch_game(self, *, user_id, balance, game_uid, currency_code, language, return_url, callback_url) -> dict:
        return {'url': f'https://mock-casino.local/play?game_uid={game_uid}&balance={balance}'}

    def fetch_demo_url(self, game_uid: str, return_url: str = '') -> str:
        return f'https://mock-casino.local/demo?game_uid={game_uid}'

    def fetch_ggr_balance(self) -> Decimal:
        return Decimal('1000')

    def decrypt_callback(self, raw_body: bytes) -> dict:
        return json.loads(raw_body.decode('utf-8'))

    def verify_callback_signature(self, key: str, timestamp: str) -> bool:
        return True


def get_casino_provider() -> BaseCasinoProvider:
    provider_name = os.getenv('CASINO_PROVIDER', 'mock').lower()
    if provider_name == 'mock':
        return MockCasinoProvider()
    if provider_name == 'softapi':
        from .providers_softapi import SoftAPIProvider
        return SoftAPIProvider()
    if provider_name == 'waija':
        from .providers_waija import WaijaProvider
        return WaijaProvider()
    raise NotImplementedError(f'Unknown casino provider: {provider_name}')
