import json
import logging
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.wallet.models import FxRateConfig

logger = logging.getLogger(__name__)

# fawazahmed0/currency-api: static JSON served off free CDNs (jsdelivr,
# with a Cloudflare Pages mirror as fallback) rather than a paid API
# service - no key, no account, no subscription to ever lapse or start
# requiring payment (unlike Open Exchange Rates, whose free tier was
# discontinued - see docs/PROJECT_MASTER_DOCUMENTATION Section 4.24).
# Data source: https://github.com/fawazahmed0/currency-api
RATE_URLS = [
    'https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usd.json',
    'https://latest.currency-api.pages.dev/v1/currencies/usd.json',
]


class Command(BaseCommand):
    help = (
        'Fetches the live USD->PKR rate from the free fawazahmed0/currency-api '
        '(no key required) and updates FxRateConfig. Skipped entirely if '
        'is_manual_override is on. On any failure the last known-good rate is '
        'left untouched - this must never block deposits.'
    )

    def _fetch_rate(self):
        last_error = None
        for url in RATE_URLS:
            request = urllib.request.Request(url, headers={'Accept': 'application/json'})
            try:
                with urllib.request.urlopen(request, timeout=20) as response:
                    body = json.loads(response.read().decode('utf-8'))
                return Decimal(str(body['usd']['pkr']))
            except (urllib.error.URLError, urllib.error.HTTPError, KeyError, ValueError, InvalidOperation) as exc:
                last_error = exc
                continue
        raise RuntimeError(f'All rate sources failed: {last_error}')

    def handle(self, *args, **options):
        config = FxRateConfig.get_solo()

        if config.is_manual_override:
            self.stdout.write('Manual override is on - skipping live fetch.')
            return

        try:
            rate = self._fetch_rate()
        except Exception as exc:
            error_message = f'FX rate fetch failed: {exc}'
            logger.warning(error_message)
            config.last_sync_error = error_message[:500]
            config.save(update_fields=['last_sync_error', 'updated_at'])
            self.stderr.write(self.style.ERROR(error_message))
            return

        config.usd_pkr_rate = rate
        config.last_synced_at = timezone.now()
        config.last_sync_error = ''
        config.save(update_fields=['usd_pkr_rate', 'last_synced_at', 'last_sync_error', 'updated_at'])
        self.stdout.write(self.style.SUCCESS(f'Updated USD->PKR rate to {rate}.'))
