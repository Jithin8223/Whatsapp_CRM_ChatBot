from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from .services import operate_connection
from .models import ConnectionOperation


@shared_task
def connection_operation(business_id, connection_id, action):
    if not settings.WPPCONNECT_ENABLED:
        return
    operate_connection(business_id, connection_id, action)


@shared_task
@transaction.atomic
def run_operation(business_id, operation_id):
    if not settings.WPPCONNECT_ENABLED:
        return
    item = ConnectionOperation.objects.select_for_update().filter(id=operation_id, business_id=business_id, connection__business_id=business_id, completed_at__isnull=True).first()
    if not item:
        return
    operate_connection(business_id, item.connection_id, item.action)
    item.attempts += 1
    item.completed_at = timezone.now()
    item.save()
