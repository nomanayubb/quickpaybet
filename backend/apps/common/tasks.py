import logging

from celery import shared_task
from django.core.management import call_command

logger = logging.getLogger(__name__)


@shared_task(ignore_result=True)
def run_database_backup() -> str:
    """
    Wraps the backup_database management command so both the nightly Celery
    Beat schedule and a manual "Trigger backup now" from the admin panel run
    it off the request/worker thread - dumping and encrypting the whole
    database can take real time, and this project's prod Gunicorn only runs
    3 workers, so doing it synchronously in a request would block one of them
    for the duration.
    """
    logger.info('Starting scheduled database backup')
    path = call_command('backup_database')
    logger.info('Database backup complete: %s', path)
    return path
