import os
from pathlib import Path
from urllib.parse import quote

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', '')
if len(SECRET_KEY) < 32 or SECRET_KEY == 'replace-with-random-secret':
    raise ImproperlyConfigured('Set a random DJANGO_SECRET_KEY of at least 32 characters.')
DEBUG = os.environ.get('DJANGO_DEBUG', 'false').lower() == 'true'
ALLOWED_HOSTS = os.environ.get('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1,backend').split(',')
INSTALLED_APPS = [
    'daphne', 'django.contrib.admin', 'django.contrib.auth',
    'django.contrib.contenttypes', 'django.contrib.sessions',
    'django.contrib.messages', 'django.contrib.staticfiles',
    'rest_framework', 'channels',
    *[f'apps.{name}' for name in (
        'accounts', 'tenants', 'whatsapp', 'conversations', 'crm_integrations',
        'products', 'ai_engine', 'knowledge_base', 'analytics', 'notifications',
    )],
]
MIDDLEWARE = [
    'config.middleware.TrustedClientIPMiddleware',
    'config.middleware.BodyLimitMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]
ROOT_URLCONF = 'config.urls'
ASGI_APPLICATION = 'config.asgi.application'
WSGI_APPLICATION = 'config.wsgi.application'
TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [], 'APP_DIRS': True,
    'OPTIONS': {'context_processors': [
        'django.template.context_processors.request',
        'django.contrib.auth.context_processors.auth',
        'django.contrib.messages.context_processors.messages',
    ]},
}]
from .database import database_settings  # noqa: E402
# Local Docker uses POSTGRES_*; staging/production set DATABASE_URL (e.g. Supabase) — see docs/PRODUCTION.md.
DATABASES = {'default': database_settings(os.environ, DEBUG)}
AUTH_USER_MODEL = 'accounts.User'
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': f'django.contrib.auth.password_validation.{name}'} for name in (
        'UserAttributeSimilarityValidator', 'MinimumLengthValidator',
        'CommonPasswordValidator', 'NumericPasswordValidator',
    )
]
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': ['apps.accounts.authentication.CookieJWTAuthentication'],
    'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.IsAuthenticated'],
    'DEFAULT_THROTTLE_CLASSES': ['rest_framework.throttling.AnonRateThrottle', 'rest_framework.throttling.UserRateThrottle'],
    'DEFAULT_THROTTLE_RATES': {'anon': '60/min', 'user': '600/min'},
}
REDIS_HOST = os.environ.get('REDIS_HOST', 'redis')
REDIS_PASSWORD = quote(os.environ['REDIS_PASSWORD'], safe='')
REDIS_URL = f'redis://:{REDIS_PASSWORD}@{REDIS_HOST}:6379'
CACHES = {'default': {
    'BACKEND': 'django.core.cache.backends.redis.RedisCache',
    'LOCATION': f'{REDIS_URL}/3',
    'OPTIONS': {'socket_connect_timeout': 2, 'socket_timeout': 2},
}}
CHANNEL_LAYERS = {'default': {
    'BACKEND': 'channels_redis.core.RedisChannelLayer',
    'CONFIG': {'hosts': [f'{REDIS_URL}/2']},
}}
CELERY_BROKER_URL = f'{REDIS_URL}/0'
CELERY_RESULT_BACKEND = f'{REDIS_URL}/1'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BROKER_TRANSPORT_OPTIONS = {'visibility_timeout': 3600}
CELERY_TASK_TIME_LIMIT = 60
CELERY_TASK_SOFT_TIME_LIMIT = 50
CELERY_RESULT_EXPIRES = 3600
CELERY_BEAT_SCHEDULE = {
    'recover-durable-work': {'task': 'apps.conversations.tasks.recover_work', 'schedule': 15.0},
    'sync-whatsapp-status': {'task': 'apps.conversations.tasks.sync_connections', 'schedule': 15.0},
    'monitor-health': {'task': 'apps.notifications.tasks.monitor_health', 'schedule': 60.0},
}
WPPCONNECT_URL = os.environ.get('WPPCONNECT_URL', 'http://wppconnect:21465')
WPPCONNECT_ENABLED = os.environ.get('WPPCONNECT_ENABLED', 'false').lower() == 'true'
CRM_AI_API_KEY = os.environ.get('CRM_AI_API_KEY', '')
CRM_AI_MODEL = os.environ.get('CRM_AI_MODEL', '')
WPPCONNECT_SECRET_KEY = os.environ.get('WPPCONNECT_SECRET_KEY', '')
WPPCONNECT_WEBHOOK_SECRET = os.environ.get('WPPCONNECT_WEBHOOK_SECRET', '')
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_TRUSTED_ORIGINS = os.environ.get('CSRF_TRUSTED_ORIGINS', 'http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000').split(',')
DATA_UPLOAD_MAX_MEMORY_SIZE = 262144
REST_FRAMEWORK.update({'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination', 'PAGE_SIZE': 50, 'EXCEPTION_HANDLER': 'config.exceptions.api_exception_handler',
                       # Throttle on REMOTE_ADDR (set from a trusted proxy header only), never on a
                       # client-supplied X-Forwarded-For, which any caller could spoof.
                       'NUM_PROXIES': 0})
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
SECURE_REFERRER_POLICY = 'same-origin'
SECURE_CROSS_ORIGIN_OPENER_POLICY = 'same-origin'


def env_flag(name, default=False):
    return os.environ.get(name, str(default)).lower() == 'true'


# ---- Reverse proxy / Cloudflare (all off for local development) -----------------------
# TLS terminates at Cloudflare; the tunnel/nginx forwards X-Forwarded-Proto: https.
if env_flag('TRUST_PROXY_HTTPS_HEADER'):
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
# Prefer Cloudflare "Always Use HTTPS"; if enabled here, internal HTTP calls stay exempt.
SECURE_SSL_REDIRECT = env_flag('SECURE_SSL_REDIRECT')
SECURE_REDIRECT_EXEMPT = [r'^api/health/', r'^internal/']
SECURE_HSTS_SECONDS = int(os.environ.get('SECURE_HSTS_SECONDS', '0'))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_flag('SECURE_HSTS_INCLUDE_SUBDOMAINS')
SECURE_HSTS_PRELOAD = env_flag('SECURE_HSTS_PRELOAD')
TRUSTED_CLIENT_IP_HEADER = os.environ.get('TRUSTED_CLIENT_IP_HEADER', '')  # e.g. CF-Connecting-IP
TRUSTED_PROXY_NETWORKS = [item.strip() for item in os.environ.get(
    'TRUSTED_PROXY_NETWORKS', '127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16').split(',') if item.strip()]
if not DEBUG and '*' in ALLOWED_HOSTS:
    raise ImproperlyConfigured('DJANGO_ALLOWED_HOSTS must list explicit host names in production.')
# Optional: token for the detailed /api/health/status/ endpoint (external uptime monitors).
HEALTH_CHECK_TOKEN = os.environ.get('HEALTH_CHECK_TOKEN', '')
# Optional HTTPS webhook (Slack/Teams/etc.) for critical health alerts; counts only, no customer data.
ALERT_WEBHOOK_URL = os.environ.get('ALERT_WEBHOOK_URL', '')
# ---- Private attachments ---------------------------------------------------------------------
# local: private Docker volume (never served by nginx). supabase: a PRIVATE Storage bucket.
ATTACHMENT_STORAGE = os.environ.get('ATTACHMENT_STORAGE', 'local')
ATTACHMENT_ROOT = os.environ.get('ATTACHMENT_ROOT', '/data/attachments')
ATTACHMENT_MAX_BYTES = int(os.environ.get('ATTACHMENT_MAX_BYTES', str(10 * 1024 * 1024)))
SUPABASE_URL = os.environ.get('SUPABASE_URL', '')
SUPABASE_SERVICE_ROLE_KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')  # server-side only
SUPABASE_STORAGE_BUCKET = os.environ.get('SUPABASE_STORAGE_BUCKET', 'relay-attachments')
if ATTACHMENT_STORAGE not in ('local', 'supabase'):
    raise ImproperlyConfigured('ATTACHMENT_STORAGE must be "local" or "supabase".')
if ATTACHMENT_STORAGE == 'supabase' and not (SUPABASE_URL.startswith('https://') and SUPABASE_SERVICE_ROLE_KEY):
    raise ImproperlyConfigured('Supabase attachment storage needs SUPABASE_URL (https) and SUPABASE_SERVICE_ROLE_KEY.')
LOGGING = {
    'version': 1, 'disable_existing_loggers': False,
    'formatters': {'plain': {'format': '%(asctime)s %(levelname)s %(name)s %(message)s'}},
    'handlers': {'console': {'class': 'logging.StreamHandler', 'formatter': 'plain'}},
    'root': {'handlers': ['console'], 'level': 'INFO'},
    'loggers': {
        'httpx': {'level': 'WARNING'}, 'httpcore': {'level': 'WARNING'},
        # Pipeline logs (webhook -> reply -> send) stay visible in the worker; Celery's own
        # per-task chatter from the 15-second beat stays quiet.
        'celery': {'level': 'WARNING'}, 'apps': {'level': 'INFO'},
    },
}
# Use the LOGGING config above in the worker instead of Celery's --loglevel root override.
CELERY_WORKER_HIJACK_ROOT_LOGGER = False
