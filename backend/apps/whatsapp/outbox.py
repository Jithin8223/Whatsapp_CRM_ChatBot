import logging
from django.db import transaction
from apps.notifications.events import publish
from apps.tenants.models import AuditEvent

logger = logging.getLogger(__name__)


@transaction.atomic
def queue_operation(connection, action, actor):
    from .models import Connection, ConnectionOperation
    Connection.objects.select_for_update().get(id=connection.id, business_id=connection.business_id)
    if ConnectionOperation.objects.filter(connection=connection, completed_at__isnull=True).exists():
        from rest_framework.exceptions import ValidationError
        raise ValidationError('A connection operation is already pending.')
    operation = ConnectionOperation.objects.create(connection=connection, business_id=connection.business_id, action=action)
    connection.desired_state = 'open' if action == 'start' else 'closed'
    connection.save(update_fields=['desired_state'])
    AuditEvent.objects.create(business=connection.business, actor=actor, action=f'connection.{action}', object_id=str(connection.id))
    publish(connection.business_id, 'connection.updated', connection.id)
    transaction.on_commit(lambda: enqueue_operation(operation))


def enqueue_operation(operation):
    from .tasks import run_operation
    try:
        run_operation.delay(str(operation.business_id), str(operation.id))
    except Exception:
        logger.warning('Connection operation persisted; broker retry pending.')
