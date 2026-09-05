import logging

from celery import shared_task
from django.utils import timezone

from apps.sports.models import Match
from .models import Bet
from .services import settle_bets_for_match

logger = logging.getLogger(__name__)


@shared_task
def settle_match(match_id: int) -> int:
    try:
        match = Match.objects.get(pk=match_id)
    except Match.DoesNotExist:
        logger.error('Match %s not found for settlement', match_id)
        return 0

    if match.status != Match.Status.FINISHED:
        logger.info('Match %s is not finished, skipping settlement', match_id)
        return 0

    if match.home_score is None or match.away_score is None:
        logger.info('Match %s has no final score, cannot settle', match_id)
        return 0

    pending_ids = list(
        Bet.objects.filter(match=match, status=Bet.Status.PENDING)
        .values_list('id', flat=True)
    )
    if not pending_ids:
        logger.info('Match %s has no pending bets', match_id)
        return 0

    settle_bets_for_match(match)
    logger.info('Settled %s bet(s) for match %s', len(pending_ids), match_id)
    return len(pending_ids)


@shared_task
def settle_matches_with_results() -> int:
    match_ids = (
        Match.objects.filter(
            status=Match.Status.FINISHED,
            home_score__isnull=False,
            away_score__isnull=False,
            bets__status=Bet.Status.PENDING,
        )
        .distinct()
        .values_list('id', flat=True)
    )

    total_settled = 0
    for match_id in match_ids:
        try:
            total_settled += settle_match(match_id)
        except Exception as exc:
            logger.exception('Failed to settle match %s: %s', match_id, exc)

    return total_settled


@shared_task
def mark_starting_matches_live():
    """
    Automatically switches scheduled matches to LIVE once their start_time has passed.
    This ensures that odds are frozen and no new bets can be placed after the game begins.
    """
    now = timezone.now()
    started_matches = Match.objects.filter(
        status=Match.Status.SCHEDULED,
        start_time__lte=now,
    )
    updated_count = started_matches.update(status=Match.Status.LIVE)
    if updated_count:
        logger.info('Marked %s match(es) as LIVE', updated_count)
    return updated_count
