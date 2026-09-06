import logging

from celery import shared_task

from apps.sports.models import Match
from apps.exchange.services import settle_exchange_for_match
from .models import Bet
from .services import settle_bets_for_match

logger = logging.getLogger(__name__)


@shared_task
def settle_match(match_id: int) -> int:
    """
    Settle single bets and parlay legs for one finished match.

    Returns a count of pending single bets that were processed, or 1 if only
    parlay legs were pending (so the periodic task knows work was done).
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

    pending_bet_count = Bet.objects.filter(
        match=match,
        status=Bet.Status.PENDING,
    ).count()

    # This also settles any pending parlay legs for this match
    settle_bets_for_match(match)
    settle_exchange_for_match(match)

    if pending_bet_count == 0:
        # There were no single bets, but parlay legs may have needed settlement.
        return 1

    return pending_bet_count


@shared_task
def settle_matches_with_results() -> int:
    """
    Find all finished matches that have final scores and call the settlement task
    for each one. This is safe to run repeatedly because settlement only affects
    bets/legs that are still pending.
    """
    match_ids = (
        Match.objects.filter(
            status=Match.Status.FINISHED,
            home_score__isnull=False,
            away_score__isnull=False,
        )
        .values_list('id', flat=True)
        .distinct()
    )

    total_settled = 0
    for match_id in match_ids:
        try:
            total_settled += settle_match(match_id)
        except Exception as exc:  # noqa: BLE001 – one failure should not stop the rest
            logger.exception('Failed to settle match %s: %s', match_id, exc)

    return total_settled


@shared_task
def mark_starting_matches_live():
    """
    Automatically switches scheduled matches to LIVE once their start_time has passed.
    This ensures that odds are frozen and no new bets can be placed after the game begins.
    """
    from django.utils import timezone

    now = timezone.now()
    updated_count = Match.objects.filter(
        status=Match.Status.SCHEDULED,
        start_time__lte=now,
    ).update(status=Match.Status.LIVE)

    if updated_count:
        logger.info('Marked %s match(es) as LIVE', updated_count)
    return updated_count
