import logging

from celery import shared_task
from django.utils import timezone

from apps.sports.models import Match
from .models import Bet
from .services import settle_bets_for_match

logger = logging.getLogger(__name__)


@shared_task
def settle_match(match_id: int) -> int:
    """
    Settle all pending bets for one finished match.
    Returns the number of bets settled.
    """
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
    """
    Find all finished matches that still have pending bets and settle them.
    Runs periodically via Celery Beat.
    """
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
        except Exception as exc:  # noqa: BLE001 - log and continue with next match
            logger.exception('Failed to settle match %s: %s', match_id, exc)

    return total_settled
