from django.core.management.base import BaseCommand
from django.utils.dateparse import parse_datetime

from apps.sports.models import Sport, Tournament, Match
from apps.sports.providers import get_odds_provider


class Command(BaseCommand):
    help = 'Fetch matches/odds from the configured provider and upsert them.'

    def _process_match(self, sport, item, created_counter, updated_counter):
        tournament_name = item.get('tournament_name', '').strip()
        if not tournament_name:
            tournament_name = sport.name

        tournament, _ = Tournament.objects.get_or_create(
            sport=sport,
            name=tournament_name,
            season=item.get('season', ''),
        )

        start_time = parse_datetime(item['start_time'])
        if not start_time:
            self.stderr.write(f"Invalid start_time: {item['start_time']}")
            return created_counter, updated_counter

        defaults = {
            'tournament': tournament,
            'odds_home': item['odds_home'],
            'odds_draw': item['odds_draw'],
            'odds_away': item['odds_away'],
            'status': Match.Status.SCHEDULED,
        }

        _, was_created = Match.objects.update_or_create(
            sport=sport,
            home_team=item['home_team'],
            away_team=item['away_team'],
            start_time=start_time,
            defaults=defaults,
        )
        if was_created:
            created_counter += 1
        else:
            updated_counter += 1
        return created_counter, updated_counter

    def handle(self, *args, **options):
        provider = get_odds_provider()
        self.stdout.write(f'Fetching matches from provider: {provider.name}')

        created = 0
        updated = 0

        if provider.name == 'mock':
            matches_data = provider.fetch_matches()
            for item in matches_data:
                sport, _ = Sport.objects.get_or_create(
                    slug=item['sport_slug'],
                    defaults={
                        'name': item['sport_name'],
                        'provider_key': item.get('sport_slug', ''),
                    },
                )
                created, updated = self._process_match(sport, item, created, updated)
        else:
            for sport in Sport.objects.filter(is_active=True).order_by('name'):
                sport_key = sport.provider_key.strip()
                if not sport_key:
                    self.stdout.write(
                        self.style.WARNING(
                            f'Skipping sport "{sport.name}" because it has no provider_key.'
                        )
                    )
                    continue

                self.stdout.write(
                    f'Fetching sport "{sport.name}" with provider_key "{sport_key}"'
                )
                try:
                    sport_matches = provider.fetch_matches(sport_key=sport_key)
                except Exception as exc:
                    self.stderr.write(
                        self.style.ERROR(
                            f'Failed to fetch "{sport.name}": {exc}'
                        )
                    )
                    continue

                for item in sport_matches:
                    created, updated = self._process_match(sport, item, created, updated)

        self.stdout.write(self.style.SUCCESS(
            f'Finished: {created} created, {updated} updated.'
        ))
