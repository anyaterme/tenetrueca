"""Read-only pre/post migration checks against the explicitly isolated staging profile."""
import json
import hashlib
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


class Command(BaseCommand):
    help = 'Check isolated staging identity and migration integrity; never applies migrations.'

    def add_arguments(self, parser):
        parser.add_argument('--ready', action='store_true', help='Also require all migrations to be applied.')

    def handle(self, *args, **options):
        db = settings.DATABASES['default']
        checks = {
            'settings': settings.SETTINGS_MODULE == 'tenetrueca.staging_settings',
            'database_config': db['NAME'] == 'trueca_chatbot_staging' and db['HOST'] == 'staging_db'
                               and db['USER'] == 'trueca_staging',
            'sessions': settings.SESSION_ENGINE == 'django.contrib.sessions.backends.db'
                        and settings.SESSION_COOKIE_NAME == 'trueca_staging_sessionid'
                        and settings.CSRF_COOKIE_NAME == 'trueca_staging_csrftoken',
            'ai_disabled': settings.CHATBOT_PROVIDER == 'mock' and not settings.CHATBOT_EVAL_ALLOW_LIVE
                           and not settings.OPENAI_API_KEY and not settings.OPENAI_LIVE_ENABLED
                           and settings.OPENAI_EVALUATION_BACKEND == 'disabled',
            'debug_false': settings.DEBUG is False,
        }
        if not all(checks.values()):
            raise CommandError('Staging isolation failed: ' + ','.join(k for k, ok in checks.items() if not ok))
        expected = json.loads((Path(settings.BASE_DIR) / 'chatbot/staging_migrations.json').read_text())
        actual = {str(p.relative_to(settings.BASE_DIR)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(Path(settings.BASE_DIR).glob('*/migrations/*.py'))}
        if expected != actual:
            raise CommandError('Migration artifacts differ from the reviewed staging manifest; do not use --fake.')
        with connection.cursor() as cursor:
            cursor.execute('SELECT current_database(), current_user')
            identity = cursor.fetchone()
            cursor.execute('SHOW fsync')
            fsync = cursor.fetchone()[0]
            cursor.execute('SHOW synchronous_commit')
            commit = cursor.fetchone()[0]
        if identity != ('trueca_chatbot_staging', 'trueca_staging') or fsync != 'on' or commit != 'on':
            raise CommandError('Staging database identity or durability mismatch.')
        executor = MigrationExecutor(connection)
        executor.loader.check_consistent_history(connection)
        pending = [f'{m.app_label}.{m.name}' for m, _ in executor.migration_plan(executor.loader.graph.leaf_nodes())]
        if options['ready'] and pending:
            raise CommandError('Staging has pending migrations; review the plan before an authorized application.')
        self.stdout.write(json.dumps({'isolation': 'verified', 'database': identity[0], 'durability': 'on',
                                      'pending_migrations': pending, 'secret_values_printed': False}))
