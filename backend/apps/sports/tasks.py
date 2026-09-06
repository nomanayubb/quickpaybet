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
    Runs the `sync_results` management command as a Celery task so results
    are pulled from the odds provider and pending bets settled automatically,
    on the schedule defined in config/celery.py.
    """
    logger.info('Calling sync_results management command.')
    call_command('sync_results')
    return True
