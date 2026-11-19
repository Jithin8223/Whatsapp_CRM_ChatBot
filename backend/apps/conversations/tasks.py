import logging
from datetime import timedelta
from celery import shared_task
from django.conf import settings
from django.utils import timezone
from django.db import transaction
from django.db.models import F
from apps.whatsapp.models import Connection, ConnectionOperation
from apps.whatsapp.outbox import enqueue_operation
from apps.whatsapp.tasks import connection_operation
from apps.notifications.events import publish
from .models import WebhookEvent, Message
from .services import process_event, dispatch, enqueue

logger = logging.getLogger(__name__)

@shared_task
def process_webhook(business_id, event_id):
    source = WebhookEvent.objects.filter(id=event_id, business_id=business_id, connection__business_id=business_id).first()
    if not source:
        return
    # Drain in ingress order even when Celery workers receive notifications out of order.
    for _ in range(20):
        item = WebhookEvent.objects.filter(connection_id=source.connection_id, business_id=business_id, processed_at__isnull=True, attempts__lt=5).order_by('created_at', 'id').first()
        if not item:
            return
        try:
            process_event(business_id, item.id)
        except Exception as error:
            from apps.ai_engine.tasks import describe_error
            reason = describe_error(error)
            logger.error('wppconnect.event failed event=%s connection=%s attempt=%s error=%s',
                         item.id, item.connection_id, item.attempts + 1, reason)
            WebhookEvent.objects.filter(id=item.id, business_id=business_id, processed_at__isnull=True).update(
                attempts=F('attempts') + 1, error=f'Retry pending: {reason}'[:120])
            WebhookEvent.objects.filter(id=item.id, business_id=business_id, attempts__gte=5).update(
                error=f'Administrator review required: {reason}'[:120])
            return


@shared_task
def dispatch_conversation(business_id, conversation_id):
    if settings.WPPCONNECT_ENABLED:
        dispatch(business_id, conversation_id)


@shared_task
def recover_work():
    from config.health import worker_heartbeat
    worker_heartbeat()  # proves beat + broker + worker are all running
    from apps.ai_engine.models import ReplyJob
    from apps.ai_engine.tasks import run_replies
    from django.db.models import Q
    jobs = ReplyJob.objects.filter(Q(state='queued') | Q(state='running', started_at__lt=timezone.now() - timedelta(minutes=2)))
    for business, conversation in jobs.values_list('message__business_id', 'message__conversation_id').distinct()[:100]:
        enqueue(run_replies, business, conversation)
    if settings.WPPCONNECT_ENABLED:
        for item in ConnectionOperation.objects.filter(completed_at__isnull=True).order_by('created_at')[:100]:
            enqueue_operation(item)
    for item in WebhookEvent.objects.filter(processed_at__isnull=True, attempts__lt=5).order_by('created_at')[:100]:
        enqueue(process_webhook, item.business_id, item.id)
    # Worker crash after submission must not cause a duplicate send.
    with transaction.atomic():
        for item in Message.objects.select_for_update(skip_locked=True).filter(send_state='sending', sending_at__lt=timezone.now() - timedelta(minutes=2))[:100]:
            item.send_state = 'unknown'
            item.last_error = 'Worker interrupted. Verify delivery in WhatsApp before resolving.'
            item.save(update_fields=['send_state', 'last_error'])
            publish(item.business_id, 'message.updated', item.conversation_id)
    if settings.WPPCONNECT_ENABLED:
        for business, conversation in Message.objects.filter(send_state='queued', next_attempt_at__lte=timezone.now()).values_list('business_id', 'conversation_id').distinct()[:100]:
            enqueue(dispatch_conversation, business, conversation)


@shared_task
def sync_connections():
    if not settings.WPPCONNECT_ENABLED:
        return
    for item in Connection.objects.filter(provider='wppconnect', removed_at__isnull=True).exclude(token_encrypted='').order_by('checked_at')[:100]:
        enqueue(connection_operation, item.business_id, item.id, 'sync')
