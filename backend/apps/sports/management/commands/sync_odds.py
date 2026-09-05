from datetime import datetime

from django.core.management.base import BaseCommand
from django.utils.dateparse import parse_datetime

from apps.sports.models import Sport, Tournament, Match
from apps.sports.providers import get_odds_provider


class Command(BaseCommand):
    help = 'Fetch matches/odds from the configured provider and upsert them.'

    def handle(self, *args, **options):
        provider = get_odds_provider()
        self.stdout.write(f'Fetching matches from provider: {provider.name}')

        matches_data = provider.fetch_matches()
        created = 0
        updated = 0

        for item in matches_data:
            sport, _ = Sport.objects.get_or_create(
                slug=item['sport_slug'],
                defaults={'name': item['sport_name']}
            )

            tournament, _ = Tournament.objects.get_or_create(
                sport=sport,
                name=item['tournament_name'],
                season=item.get('season', ''),
            )

            start_time = parse_datetime(item['start_time'])
            if not start_time:
                self.stderr.write(f"Invalid start_time: {item['start_time']}")
                continue

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
                created += 1
            else:
                updated += 1

        self.stdout.write(self.style.SUCCESS(
            f'Finished: {created} created, {updated} updated.'
        ))
