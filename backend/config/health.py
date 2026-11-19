import hmac
import logging
import time

import httpx
from django.conf import settings
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from redis import Redis

logger = logging.getLogger(__name__)


def dependency_checks():
    checks = {}
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            checks['postgres'] = cursor.fetchone()[0] == 1
    except Exception:
        logger.warning('PostgreSQL readiness check failed')
        checks['postgres'] = False
    try:
        with Redis.from_url(f'{settings.REDIS_URL}/0', socket_connect_timeout=2, socket_timeout=2) as client:
            checks['redis'] = bool(client.ping())
    except Exception:
        logger.warning('Redis readiness check failed')
        checks['redis'] = False
    return checks


WORKER_HEARTBEAT_KEY = 'health:worker_heartbeat'
WORKER_STALE_SECONDS = 90


def worker_heartbeat():
    """Called by the Celery beat-driven recovery task every 15 s."""
    from django.core.cache import cache
    cache.set(WORKER_HEARTBEAT_KEY, time.time(), timeout=3600)


def detailed_checks():
    """Operational status for monitors. Counts and booleans only — no customer data."""
    from datetime import timedelta
    from django.core.cache import cache
    from django.utils import timezone
    checks = dependency_checks()
    report = {'checks': checks, 'metrics': {}, 'warnings': []}
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()')
            row = cursor.fetchone()
            report['metrics']['database_tls'] = bool(row and row[0])
    except Exception:
        report['metrics']['database_tls'] = None
    try:
        beat = cache.get(WORKER_HEARTBEAT_KEY)
    except Exception:
        beat = None
    age = None if beat is None else int(time.time() - beat)
    report['metrics']['worker_heartbeat_age_seconds'] = age
    checks['celery_worker'] = age is not None and age <= WORKER_STALE_SECONDS
    if checks.get('postgres'):
        try:
            from apps.ai_engine.models import ReplyJob
            from apps.conversations.models import Message, WebhookEvent
            from apps.whatsapp.models import Connection
            hour = timezone.now() - timedelta(hours=1)
            webhooks = WebhookEvent.objects.filter(processed_at__isnull=True)
            report['metrics'].update({
                'webhook_backlog': webhooks.count(),
                'webhook_stuck': webhooks.filter(created_at__lt=timezone.now() - timedelta(minutes=5)).count(),
                'webhook_failed': webhooks.filter(attempts__gte=5).count(),
                'failed_replies_last_hour': ReplyJob.objects.filter(state='failed', finished_at__gte=hour).count(),
                'unknown_sends': Message.objects.filter(send_state='unknown').count(),
                'queued_sends_older_than_5m': Message.objects.filter(
                    send_state='queued', created_at__lt=timezone.now() - timedelta(minutes=5)).count(),
            })
            sessions = Connection.objects.filter(provider='wppconnect', removed_at__isnull=True, desired_state='open')
            report['metrics']['whatsapp_sessions_expected'] = sessions.count()
            report['metrics']['whatsapp_sessions_connected'] = sessions.filter(state='connected').count()
            if report['metrics']['whatsapp_sessions_connected'] < report['metrics']['whatsapp_sessions_expected']:
                report['warnings'].append('whatsapp_session_disconnected')
            if report['metrics']['webhook_stuck'] or report['metrics']['webhook_failed']:
                report['warnings'].append('webhook_processing_delayed')
            if report['metrics']['failed_replies_last_hour']:
                report['warnings'].append('automatic_replies_failing')
        except Exception:
            logger.warning('Health metrics query failed')
            report['warnings'].append('metrics_unavailable')
    report['status'] = 'ok' if all(checks.values()) and not report['warnings'] else (
        'degraded' if all(checks.values()) else 'critical')
    return report


@require_GET
def status(request):
    """Detailed status for uptime monitors: requires HEALTH_CHECK_TOKEN (Bearer) outside DEBUG."""
    token = settings.HEALTH_CHECK_TOKEN
    supplied = request.headers.get('Authorization', '').removeprefix('Bearer ').strip()
    if not settings.DEBUG and (not token or not hmac.compare_digest(supplied, token)):
        return JsonResponse({'detail': 'Not found.'}, status=404)
    report = detailed_checks()
    return JsonResponse(report, status=503 if report['status'] == 'critical' else 200)


@require_GET
def live(request):
    return JsonResponse({'status': 'ok', 'service': 'django', 'phase': 1})


@require_GET
def ready(request):
    checks = dependency_checks()
    healthy = all(checks.values())
    return JsonResponse({'status': 'ok' if healthy else 'degraded', 'checks': checks}, status=200 if healthy else 503)


@require_GET
def infrastructure(request):
    # Local development only: production health endpoints expose minimal status.
    if not settings.DEBUG:
        return JsonResponse({'detail': 'Not found.'}, status=404)
    checks = dependency_checks()
    try:
        response = httpx.get(f'{settings.WPPCONNECT_URL}/healthz', timeout=3, follow_redirects=False)
        checks['wppconnect'] = response.is_success
    except httpx.HTTPError:
        checks['wppconnect'] = False
    healthy = all(checks.values())
    return JsonResponse({'phase': 1, 'status': 'ok' if healthy else 'degraded', 'checks': checks}, status=200 if healthy else 503)
