from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils.dateparse import parse_datetime

from apps.sports.models import OddsAdjustmentConfig, Sport, Tournament, Match
from apps.sports.pricing import apply_odds_adjustment, normalize_odds, resolve_effective_adjustment
from apps.sports.providers import get_odds_provider


class Command(BaseCommand):
    help = 'Fetch matches/odds from the configured provider, normalize them, and upsert.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--margin',
            type=float,
            default=0.05,
            help='Target overround margin (e.g. 0.05 for 5%).',
        )
        parser.add_argument(
            '--sport',
            dest='sport_provider_key',
            default=None,
            help='Only sync odds for this provider key (e.g. soccer_epl).',
        )

    def _process_match(
        self,
        sport,
        item,
        created_counter,
        updated_counter,
        margin: Decimal,
        default_adjustment: Decimal,
    ):
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

        try:
            odds_home, odds_draw, odds_away = normalize_odds(
                item['odds_home'],
                item['odds_draw'],
                item['odds_away'],
                margin=margin,
            )
        except (ValueError, ZeroDivisionError) as exc:
            self.stderr.write(f"Skipping {item['home_team']} v {item['away_team']}: {exc}")
            return created_counter, updated_counter

        # Look up any existing match first so a per-match odds_adjustment
        # override (set by an admin) survives this sync instead of being
        # silently ignored - update_or_create's `defaults` never touch a
        # field that isn't listed in it, but we still need the existing
        # value here to compute the effective adjustment to apply.
        existing = Match.objects.filter(
            sport=sport,
            home_team=item['home_team'],
            away_team=item['away_team'],
            start_time=start_time,
        ).first()
        match_adjustment = existing.odds_adjustment if existing else None
        effective_adjustment = resolve_effective_adjustment(match_adjustment, default_adjustment)
        odds_home, odds_draw, odds_away = apply_odds_adjustment(
            effective_adjustment, odds_home, odds_draw, odds_away,
        )

        defaults = {
            'tournament': tournament,
            'odds_home': odds_home,
            'odds_draw': odds_draw,
            'odds_away': odds_away,
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
        margin = Decimal(str(options.get('margin', 0.05)))
        only_provider_key = options.get('sport_provider_key')
        default_adjustment = OddsAdjustmentConfig.get_solo().default_adjustment
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
                created, updated = self._process_match(
                    sport,
                    item,
                    created,
                    updated,
                    margin,
                    default_adjustment,
                )
        else:
            sports_qs = Sport.objects.filter(is_active=True).order_by('name')
            if only_provider_key:
                sports_qs = sports_qs.filter(provider_key=only_provider_key)

            for sport in sports_qs:
                sport_key = sport.provider_key.strip()
                if not sport_key:
                    self.stdout.write(
                        self.style.WARNING(
                            f'Skipping sport "{sport.name}" because it has no provider_key.'
                        )
                    )
                    continue

                if sport_key.lower() in provider.invalid_sport_keys:
                    self.stdout.write(
                        self.style.WARNING(
                            f'Skipping sport "{sport.name}" because "{sport_key}" is not valid for {provider.name}.'
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
                    created, updated = self._process_match(
                        sport,
                        item,
                        created,
                        updated,
                        margin,
                    )

        self.stdout.write(self.style.SUCCESS(
            f'Finished: {created} created, {updated} updated.'
        ))
