from django.core.management.base import BaseCommand

from apps.sports.models import Match
from apps.bets.services import settle_bets_for_match


class Command(BaseCommand):
    help = 'Mark a match as finished with a score and settle all associated bets.'

    def add_arguments(self, parser):
        parser.add_argument('--match_id', type=int, required=True, help='Match ID to finish')
        parser.add_argument('--home_score', type=int, required=True, help='Final home score')
        parser.add_argument('--away_score', type=int, required=True, help='Final away score')

    def handle(self, *args, **options):
        match_id = options['match_id']
        home_score = options['home_score']
        away_score = options['away_score']

        try:
            match = Match.objects.get(pk=match_id)
        except Match.DoesNotExist:
            self.stderr.write(self.style.ERROR(f'Match {match_id} does not exist.'))
            return

        if match.status == Match.Status.FINISHED:
            self.stdout.write(self.style.WARNING(f'Match {match_id} is already finished.'))
            return

        match.home_score = home_score
        match.away_score = away_score
        match.status = Match.Status.FINISHED
        match.save(update_fields=['home_score', 'away_score', 'status', 'updated_at'])

        settle_bets_for_match(match)
        self.stdout.write(self.style.SUCCESS(
            f'Marked match {match_id} as FINISHED ({home_score}:{away_score}) and settled bets.'
        ))
