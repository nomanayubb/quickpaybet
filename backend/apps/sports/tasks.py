import logging

from celery import shared_task
from django.core.management import call_command

logger = logging.getLogger(__name__)


@shared_task
def sync_odds_celery():
    """
    Runs the `sync_odds` management command as a Celery task so it can be
    scheduled on a recurring basis via Celery Beat.
    """
    logger.info('Calling sync_odds management command.')
    call_command('sync_odds')
    return True


@shared_task
def sync_results_celery():
    """
    Placeholder Celery task that periodically runs `sync_results`.

    In production, the actual result source (API/keyboard entry) would provide
    match IDs and scores. This task keeps the command accessible to Celery Beat
    but does not fetch arbitrary remote results automatically.
    """
    logger.info('Scheduled sync_results task called.')
    return True
