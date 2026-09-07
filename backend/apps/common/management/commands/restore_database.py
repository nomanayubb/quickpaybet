import tempfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = (
        'Decrypt a backup produced by backup_database and load it into the '
        'current database via loaddata. Destructive by nature (it writes '
        'rows with fixed primary keys on top of whatever is already there) - '
        'intended for a single use, on a freshly migrated and otherwise-empty '
        'database on a newly provisioned server. Refuses to run without '
        '--confirm.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            'backup_file', type=str,
            help='Path to a .json.enc file, either absolute or relative to BACKUP_DIR.',
        )
        parser.add_argument(
            '--confirm', action='store_true',
            help='Required. Confirms you intend to load this backup into the current database.',
        )

    def handle(self, *args, **options):
        if not options['confirm']:
            raise CommandError(
                'Refusing to restore without --confirm. This overwrites data in the current '
                'database - only run it on a freshly migrated, empty database on a new server.'
            )

        key = settings.BACKUP_ENCRYPTION_KEY
        if not key:
            raise CommandError('BACKUP_ENCRYPTION_KEY is not set - cannot decrypt the backup.')
        try:
            fernet = Fernet(key.encode())
        except ValueError as exc:
            raise CommandError(f'BACKUP_ENCRYPTION_KEY is not a valid Fernet key: {exc}')

        backup_path = Path(options['backup_file'])
        if not backup_path.is_absolute():
            candidate = settings.BACKUP_DIR / backup_path
            backup_path = candidate if candidate.exists() else backup_path
        if not backup_path.exists():
            raise CommandError(f'Backup file not found: {backup_path}')

        encrypted = backup_path.read_bytes()
        try:
            plaintext = fernet.decrypt(encrypted)
        except InvalidToken:
            raise CommandError(
                'Could not decrypt this backup - either it is corrupted, or '
                'BACKUP_ENCRYPTION_KEY does not match the key it was encrypted with.'
            )

        with tempfile.NamedTemporaryFile(mode='wb', suffix='.json', delete=False) as tmp:
            tmp.write(plaintext)
            tmp_path = tmp.name

        try:
            call_command('loaddata', tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        self.stdout.write(self.style.SUCCESS(f'Restored from {backup_path}'))
