import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('quickpaybet')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

app.conf.beat_schedule = {
    'settle-finished-matches': {
        'task': 'apps.bets.tasks.settle_matches_with_results',
        'schedule': crontab(minute='*/5'),
    },
    'mark-starting-matches-live': {
        'task': 'apps.bets.tasks.mark_starting_matches_live',
        'schedule': crontab(minute='*'),
    },
    'sync-odds-recurring': {
        'task': 'apps.sports.tasks.sync_odds_celery',
        'schedule': crontab(hour='*/6', minute=0),
    },
    'sync-results-recurring': {
        'task': 'apps.sports.tasks.sync_results_celery',
        'schedule': crontab(minute='*/30'),
    },
}
