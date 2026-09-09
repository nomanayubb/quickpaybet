from django.core.management.base import BaseCommand

from apps.casino.services import sync_catalog


class Command(BaseCommand):
    """
    Manual-trigger only - do not wire this into Celery Beat or any other
    automatic schedule. Same caution already applied to ParlayAPI: this
    provider is also rate-limited (20 req/min) and every real call should
    be a single, deliberate one until the integration is fully tested.
    """
    help = 'Pulls the casino provider/game catalog and upserts it (manual trigger only).'

    def handle(self, *args, **options):
        result = sync_catalog()
        self.stdout.write(self.style.SUCCESS(
            f"Synced {result['brands']} brand(s), "
            f"{result['games_created']} new game(s), {result['games_updated']} updated game(s)."
        ))
