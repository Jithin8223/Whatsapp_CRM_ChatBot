import logging

import httpx
from celery import shared_task
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)
ALERT_STATE_KEY = 'health:last_alert_state'


@shared_task
def infrastructure_ping():
    """Harmless end-to-end broker/worker/result-backend smoke test."""
    return {'status': 'ok', 'service': 'celery'}


@shared_task
def monitor_health():
    """Log critical/degraded health and notify ALERT_WEBHOOK_URL on state changes only."""
    from config.health import detailed_checks
    report = detailed_checks()
    failing = sorted(name for name, ok in report['checks'].items() if not ok)
    state = f"{report['status']}:{','.join(failing + report['warnings'])}"
    if report['status'] != 'ok':
        logger.error('health.%s failing=%s warnings=%s', report['status'], failing, report['warnings'])
    try:
        previous = cache.get(ALERT_STATE_KEY)
        cache.set(ALERT_STATE_KEY, state, timeout=86400)
    except Exception:
        previous = None
    if state == previous or not settings.ALERT_WEBHOOK_URL.startswith('https://'):
        return report['status']
    if previous is None and report['status'] == 'ok':
        return report['status']
    text = (f"Relay CRM health is {report['status'].upper()}. Failing: {', '.join(failing) or 'none'}. "
            f"Warnings: {', '.join(report['warnings']) or 'none'}.")
    try:
        httpx.post(settings.ALERT_WEBHOOK_URL, json={'text': text, 'status': report['status'], 'failing': failing,
                                                     'warnings': report['warnings']}, timeout=5, follow_redirects=False)
    except httpx.HTTPError:
        logger.warning('health.alert_delivery_failed')
    return report['status']
