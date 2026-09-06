from django.core.management.base import BaseCommand
from django.utils.dateparse import parse_datetime

from apps.sports.models import Sport, Match
from apps.sports.providers import get_odds_provider, INVALID_PROVIDER_KEYS
from apps.bets.services import settle_bets_for_match
from apps.exchange.services import settle_exchange_for_match


class Command(BaseCommand):
    help = 'Fetch match results from the configured provider, update local matches, and settle bets.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--sport',
            dest='sport_provider_key',
            default=None,
            help='Only sync results for this provider key (e.g. soccer_epl).',
        )

    def _handle_sport(self, sport, provider, updated):
        sport_key = sport.provider_key.strip()
        if not sport_key:
            self.stdout.write(
                self.style.WARNING(
                    f'Skipping sport "{sport.name}" because it has no provider_key.'
                )
            )
            return updated

        if sport_key.lower() in INVALID_PROVIDER_KEYS:
            self.stdout.write(
                self.style.WARNING(
                    f'Skipping sport "{sport.name}" because "{sport_key}" is not valid for The Odds API.'
                )
            )
            return updated

        self.stdout.write(f'Fetching results for sport "{sport.name}" ({sport_key})')
        try:
            results = provider.fetch_scores(sport_key=sport_key)
        except Exception as exc:
            self.stderr.write(
                self.style.ERROR(f'Failed to fetch results for "{sport.name}": {exc}')
            )
            return updated

        for item in results:
            start_time = parse_datetime(item['start_time'])
            if not start_time:
                self.stderr.write(f"Invalid start_time: {item['start_time']}")
                continue

            match = Match.objects.filter(
                sport=sport,
                home_team=item['home_team'],
                away_team=item['away_team'],
                start_time=start_time,
            ).first()

            if not match:
                continue

            if not item.get('completed'):
                continue

            if match.status == Match.Status.FINISHED:
                continue

            match.status = Match.Status.FINISHED
            match.home_score = item.get('home_score')
            match.away_score = item.get('away_score')
            match.save(update_fields=['status', 'home_score', 'away_score', 'updated_at'])

            settle_bets_for_match(match)
            settle_exchange_for_match(match)
            updated += 1

        return updated

    def handle(self, *args, **options):
        provider = get_odds_provider()
        self.stdout.write(f'Syncing results from provider: {provider.name}')

        if provider.name == 'mock':
            self.stdout.write(self.style.WARNING('Provider is mock; skipping result sync.'))
            return

        only_provider_key = options.get('sport_provider_key')
        sports_qs = Sport.objects.filter(is_active=True).order_by('name')
        if only_provider_key:
            sports_qs = sports_qs.filter(provider_key=only_provider_key)

        updated = 0
        for sport in sports_qs:
            updated = self._handle_sport(sport, provider, updated)

        self.stdout.write(self.style.SUCCESS(
            f'Finished: {updated} match(es) updated and settled from results feed.'
        ))
