import json
import logging
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.wallet.models import FxRateConfig

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        'Fetches the live USD->PKR rate from Open Exchange Rates and updates '
        'FxRateConfig. Skipped entirely if is_manual_override is on, or if no '
        'OPEN_EXCHANGE_RATES_APP_ID is configured. On any failure the last '
        'known-good rate is left untouched - this must never block deposits.'
    )

    def handle(self, *args, **options):
        import os

        config = FxRateConfig.get_solo()

        if config.is_manual_override:
            self.stdout.write('Manual override is on - skipping live fetch.')
            return

        app_id = os.getenv('OPEN_EXCHANGE_RATES_APP_ID', '').strip()
        if not app_id:
            msg = 'OPEN_EXCHANGE_RATES_APP_ID is not configured - skipping live fetch, last known rate kept.'
            self.stdout.write(self.style.WARNING(msg))
            config.last_sync_error = msg
            config.save(update_fields=['last_sync_error', 'updated_at'])
            return

        url = f'https://openexchangerates.org/api/latest.json?app_id={app_id}&symbols=PKR'
        request = urllib.request.Request(url, headers={'Accept': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                body = json.loads(response.read().decode('utf-8'))
            rate = Decimal(str(body['rates']['PKR']))
        except (urllib.error.URLError, urllib.error.HTTPError, KeyError, ValueError, InvalidOperation) as exc:
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
