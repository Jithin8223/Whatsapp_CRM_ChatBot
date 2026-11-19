import logging
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction

logger = logging.getLogger(__name__)


def publish(business_id, kind, object_id=''):
    def emit():
        try:
            send = async_to_sync(get_channel_layer().group_send)
            send(f'business.{business_id}', {'type': 'crm.event', 'kind': kind, 'object_id': str(object_id)})
            if kind in ('message.created', 'conversation.updated'):
                send(f'guest.{object_id}', {'type': 'guest.event'})
        except Exception:
            logger.warning('Realtime unavailable; clients can refresh.')
    transaction.on_commit(emit)
