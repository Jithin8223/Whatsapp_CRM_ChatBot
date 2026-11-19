"""Read-only database connectivity check (local Docker PostgreSQL or Supabase).

    python manage.py check_database [--concurrency 8]

Never prints credentials, never writes data, never runs migrations.
"""
import time
from concurrent.futures import ThreadPoolExecutor

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, connections
from django.db.migrations.executor import MigrationExecutor


class Command(BaseCommand):
    help = 'Validate the configured database connection, TLS, migrations and concurrent connections (read-only).'

    def add_arguments(self, parser):
        parser.add_argument('--concurrency', type=int, default=8, help='Parallel connections to open (default 8).')

    def handle(self, *args, **options):
        config = connection.settings_dict
        host = config.get('HOST') or 'local socket'
        self.stdout.write(f"Host: {host}  Port: {config.get('PORT')}  Database: {config.get('NAME')}")
        self.stdout.write(f"sslmode: {config['OPTIONS'].get('sslmode', 'driver default')}  "
                          f"CONN_MAX_AGE: {config.get('CONN_MAX_AGE')}  "
                          f"server-side cursors disabled: {config.get('DISABLE_SERVER_SIDE_CURSORS', False)}")
        started = time.monotonic()
        try:
            with connection.cursor() as cursor:
                cursor.execute('SHOW server_version')
                version = cursor.fetchone()[0]
                cursor.execute('SELECT ssl, version FROM pg_stat_ssl WHERE pid = pg_backend_pid()')
                ssl_row = cursor.fetchone()
        except Exception as error:
            raise CommandError(f'Connection failed: {type(error).__name__}. Check DATABASE_URL, network access and TLS settings.')
        self.stdout.write(f'Connected in {int((time.monotonic() - started) * 1000)} ms; PostgreSQL {version}')
        tls = bool(ssl_row and ssl_row[0])
        self.stdout.write(f"TLS: {'yes (' + str(ssl_row[1]) + ')' if tls else 'NO'}")
        if not tls and config['OPTIONS'].get('sslmode') in ('require', 'verify-ca', 'verify-full'):
            raise CommandError('sslmode requires TLS but the session is not encrypted.')
        executor = MigrationExecutor(connection)
        pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
        self.stdout.write(f'Unapplied migrations: {len(pending)}' + (' (run "manage.py migrate" after reviewing)' if pending else ''))

        def probe(_):
            try:
                with connections['default'].cursor() as cursor:
                    cursor.execute('SELECT pg_sleep(0.2), 1')
                    return cursor.fetchone()[1] == 1
            finally:
                connections['default'].close()

        count = max(1, min(options['concurrency'], 50))
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=count) as pool:
            results = list(pool.map(probe, range(count)))
        elapsed = int((time.monotonic() - started) * 1000)
        if not all(results):
            raise CommandError('Concurrent connection test failed.')
        self.stdout.write(self.style.SUCCESS(f'{count} concurrent connections succeeded in {elapsed} ms.'))
