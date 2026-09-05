from django.core.management.base import BaseCommand

from apps.sports.models import Sport
from apps.sports.providers import get_odds_provider


class Command(BaseCommand):
    help = 'Sync available sports from the configured odds provider into the Sport model.'

    def handle(self, *args, **options):
        provider = get_odds_provider()
        self.stdout.write(f'Syncing sports from provider: {provider.name}')

        if provider.name == 'mock':
            self.stdout.write(self.style.WARNING('Provider is mock; skipping sport sync.'))
            return

        try:
            sports_data = provider.fetch_sports()
        except NotImplementedError:
            self.stderr.write(self.style.ERROR('Provider does not support fetch_sports().'))
            return
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f'Failed to fetch sports: {exc}'))
            return

        created = 0
        updated = 0
        for item in sports_data:
            key = item.get('key')
            title = item.get('title') or key
            is_active = item.get('active', True)

            if not key:
                self.stderr.write(self.style.WARNING(f'Skipping sport with no key: {item}'))
                continue

            sport, was_created = Sport.objects.update_or_create(
                slug=key,
                defaults={
                    'name': title,
                    'provider_key': key,
                    'is_active': is_active,
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1

        self.stdout.write(self.style.SUCCESS(
            f'Finished: {created} created, {updated} updated.'
        ))
