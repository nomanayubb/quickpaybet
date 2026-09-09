import logging

from celery import shared_task
from django.core.management import call_command

logger = logging.getLogger(__name__)


@shared_task
def refresh_usd_pkr_rate():
    """
    Runs the `refresh_fx_rate` management command as a Celery task, scheduled
    hourly (see config/celery.py) - keeps FxRateConfig.usd_pkr_rate current
    for every PKR-currency user's deposits/withdrawals/balance display.
    """
    logger.info('Calling refresh_fx_rate management command.')
    call_command('refresh_fx_rate')
    return True
