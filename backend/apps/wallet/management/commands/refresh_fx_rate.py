import json
import logging
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.wallet.models import FxRateConfig

logger = logging.getLogger(__name__)

# Three independent, free, no-key sources tried in order - independent in
# the sense that matters: two different maintainers/organizations, not just
# two CDNs mirroring the same underlying data. The jsdelivr/pages.dev pair
# both mirror fawazahmed0/currency-api (github.com/fawazahmed0/currency-api)
# - if that project ever goes stale, both would go stale together, so
# open.er-api.com (run by exchangerate-api.com, a wholly separate service)
# is kept as a genuinely independent third source. All three are free with
# no key/account/subscription - nothing here can ever start requiring
# payment the way Open Exchange Rates did (see
# docs/PROJECT_MASTER_DOCUMENTATION Section 4.24).
RATE_SOURCES = [
    ('https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usd.json',
     lambda body: body['usd']['pkr']),
    ('https://latest.currency-api.pages.dev/v1/currencies/usd.json',
     lambda body: body['usd']['pkr']),
    ('https://open.er-api.com/v6/latest/USD',
     lambda body: body['rates']['PKR']),
]


class Command(BaseCommand):
    help = (
        'Fetches the live USD->PKR rate from one of three free, no-key rate '
        'sources and updates FxRateConfig. Skipped entirely if '
        'is_manual_override is on. On any failure the last known-good rate is '
        'left untouched - this must never block deposits.'
    )

    def _fetch_rate(self):
        last_error = None
        for url, extract in RATE_SOURCES:
            # Cloudflare-fronted hosts (pages.dev here) reject Python's
            # default urllib User-Agent with a 403 - same fix already used
            # for ParlayAPI and Waija elsewhere in this project.
            request = urllib.request.Request(
                url, headers={'Accept': 'application/json', 'User-Agent': 'Mozilla/5.0 (compatible; QuickPayBet/1.0)'},
            )
            try:
                with urllib.request.urlopen(request, timeout=20) as response:
                    body = json.loads(response.read().decode('utf-8'))
                return Decimal(str(extract(body)))
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
