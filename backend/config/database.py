"""Database settings: local Docker PostgreSQL (POSTGRES_*) or a managed provider such as
Supabase via DATABASE_URL. Credentials stay in server-side environment variables only."""
from urllib.parse import parse_qs, unquote, urlsplit

from django.core.exceptions import ImproperlyConfigured

SSL_MODES = {'disable', 'allow', 'prefer', 'require', 'verify-ca', 'verify-full'}


def _int(env, name, default, minimum=0):
    try:
        value = int(env.get(name, default))
    except ValueError:
        raise ImproperlyConfigured(f'{name} must be an integer.')
    if value < minimum:
        raise ImproperlyConfigured(f'{name} must be at least {minimum}.')
    return value


def database_settings(env, debug):
    """Return Django's DATABASES['default'].

    DATABASE_URL           postgresql://USER:PASSWORD@HOST:PORT/DBNAME  (enables managed mode)
    DB_SSLMODE             require (default with DATABASE_URL) | verify-full (+ DB_SSLROOTCERT) | ...
    DB_CONN_MAX_AGE        seconds to keep connections; 0 (default) closes after each request
    DB_CONNECT_TIMEOUT     seconds, default 5
    DB_POOL_MODE           direct | session | transaction. "transaction" (Supabase port 6543)
                           disables server-side cursors, which transaction poolers cannot support.
    """
    url = env.get('DATABASE_URL', '').strip()
    timeout = _int(env, 'DB_CONNECT_TIMEOUT', 5 if url else 3, minimum=1)
    max_age = _int(env, 'DB_CONN_MAX_AGE', 0)
    if not url:
        return {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': env.get('POSTGRES_DB', 'whatsapp_crm'),
            'USER': env.get('POSTGRES_USER', 'crm'),
            'PASSWORD': env['POSTGRES_PASSWORD'],
            'HOST': env.get('POSTGRES_HOST', 'postgres'),
            'PORT': env.get('POSTGRES_PORT', '5432'),
            'CONN_MAX_AGE': max_age,
            'CONN_HEALTH_CHECKS': max_age > 0,
            'OPTIONS': {'connect_timeout': timeout, **({'sslmode': env['DB_SSLMODE']} if env.get('DB_SSLMODE') else {})},
        }
    parts = urlsplit(url)
    if parts.scheme not in ('postgres', 'postgresql') or not parts.hostname or not parts.path.strip('/'):
        raise ImproperlyConfigured('DATABASE_URL must look like postgresql://user:password@host:port/database.')
    query = {key: values[-1] for key, values in parse_qs(parts.query).items()}
    sslmode = env.get('DB_SSLMODE') or query.get('sslmode') or 'require'
    if sslmode not in SSL_MODES:
        raise ImproperlyConfigured(f'DB_SSLMODE must be one of: {", ".join(sorted(SSL_MODES))}.')
    if not debug and sslmode in ('disable', 'allow', 'prefer'):
        raise ImproperlyConfigured('Production database connections must use TLS (DB_SSLMODE=require or verify-full).')
    pool_mode = env.get('DB_POOL_MODE', 'transaction' if parts.port == 6543 else 'session').lower()
    if pool_mode not in ('direct', 'session', 'transaction'):
        raise ImproperlyConfigured('DB_POOL_MODE must be direct, session or transaction.')
    options = {'sslmode': sslmode, 'connect_timeout': timeout, 'application_name': env.get('DB_APPLICATION_NAME', 'relay-crm')}
    if env.get('DB_SSLROOTCERT'):
        options['sslrootcert'] = env['DB_SSLROOTCERT']
    return {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': unquote(parts.path.lstrip('/')),
        'USER': unquote(parts.username or ''),
        'PASSWORD': unquote(parts.password or ''),
        'HOST': parts.hostname,
        'PORT': str(parts.port or 5432),
        'CONN_MAX_AGE': max_age,
        'CONN_HEALTH_CHECKS': max_age > 0,
        # Django's psycopg3 backend binds parameters client-side, so no server-side
        # prepared statements are created; only server-side cursors need disabling.
        'DISABLE_SERVER_SIDE_CURSORS': pool_mode == 'transaction',
        'OPTIONS': options,
    }
