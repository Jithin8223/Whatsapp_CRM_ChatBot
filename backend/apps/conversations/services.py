import hashlib
import json
import logging
import time
from datetime import datetime, timezone as dt_timezone, timedelta
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from apps.notifications.events import publish
from apps.whatsapp.crypto import encrypt, decrypt
from apps.whatsapp.models import Connection
from apps.whatsapp.providers import SafeToRetry, ProviderError
from .channels import channel_for
from .mobile import mobile_cipher
from .models import Contact, Conversation, Message, MessageAttachment, DeliveryEvent, WebhookEvent

logger = logging.getLogger(__name__)


def cancel_ai_replies(conversation):
    from apps.ai_engine.models import ReplyJob
    ReplyJob.objects.filter(
        message__conversation=conversation, message__business_id=conversation.business_id,
        state__in=('queued', 'running', 'draft'),
    ).update(state='skipped', finished_at=timezone.now())
    conversation.messages.filter(automated=True, direction='outbound', send_state='queued').update(
        send_state='cancelled', last_error='Automatic reply cancelled before submission.',
    )


def enqueue(task, *args):
    try:
        task.delay(*map(str, args))
    except Exception as error:
        # The work row is already committed; recover_work (every 15 s) re-enqueues it.
        logger.error('celery.enqueue_failed task=%s error=%s; durable work saved, periodic recovery will retry.',
                     getattr(task, 'name', task), type(error).__name__)


def resumed_reply_mode(conversation, content):
    """Return 'ai' when an inbound message should (re)activate automatic replies.

    Human mode means a human owns the conversation (assigned_agent is set by every
    deliberate takeover: choosing Human, assigning an agent, or sending a reply).
    Unowned 'manual' rows, and 'waiting' rows without an explicit handoff, were
    created while automation was off, so they switch to AI. An explicitly
    handed-off conversation that no agent has claimed resumes only when the
    customer asks for the menu again.
    """
    if conversation.assigned_agent_id or conversation.reply_mode in ('ai', 'assisted'):
        return None
    if conversation.reply_mode == 'manual':
        return 'ai'
    if conversation.reply_mode == 'waiting':
        from apps.ai_engine.menu import is_menu_command
        if not conversation.handoff_at or is_menu_command(content, greetings=False):
            return 'ai'
    return None


def event_time_bucket():
    """15-second window used to collapse WPPConnect retries of status/QR events."""
    return int(time.time() // 15)


def persist_event(connection, payload):
    if payload['event'] in ('onmessage', 'unreadmessages'):
        key_data = ['message', payload['id']]
    elif payload['event'] == 'onack':
        key_data = ['ack', payload['id'], payload['ack']]
    else:
        # Status/QR events repeat legitimately (every new QR, every auto-close). Only
        # collapse WPPConnect's retries of the same delivery, which arrive within seconds.
        key_data = [payload, event_time_bucket()]
    key = hashlib.sha256(json.dumps(key_data, sort_keys=True).encode()).hexdigest()
    with transaction.atomic():
        item, created = WebhookEvent.objects.get_or_create(connection=connection, key=key, defaults={'business_id': connection.business_id, 'payload_encrypted': encrypt(json.dumps(payload))})
        if created:
            from .tasks import process_webhook
            transaction.on_commit(lambda: enqueue(process_webhook, connection.business_id, item.id))
    return created


def provider_time(value):
    try:
        return datetime.fromtimestamp(float(value), tz=dt_timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


# WhatsApp Web ACK values (WPPConnect AckType): negatives are failures, 5 (PEER) is a sync to
# the business's own linked devices and says nothing about the customer.
ACK_STATUS = {0: 'pending', 1: 'sent', 2: 'delivered', 3: 'read', 4: 'read'}
ACK_FAILURES = {
    -1: 'WhatsApp reported the message failed.', -2: 'WhatsApp reported the message expired before delivery.',
    -3: 'WhatsApp reported the attached content is no longer available.', -4: 'WhatsApp rejected the attachment as too large.',
    -5: 'WhatsApp could not upload the attachment.', -6: 'WhatsApp reported the recipient is inactive.',
    -7: 'WhatsApp could not deliver to this device version.',
}
DELIVERY_RANK = {'pending': 0, 'sent': 1, 'delivered': 2, 'read': 3}


def reconcile_delivery(message):
    """Derive the delivery status from every ack ever received for this message.

    Idempotent and order-independent: duplicate or late (older) acks can never downgrade a
    newer status, and "read" is set only from an actual read receipt (ack 3/4).
    """
    rows = list(DeliveryEvent.objects.filter(
        business_id=message.business_id, connection_id=message.connection_id, provider_id=message.provider_id,
    ).exclude(ack=5).values_list('ack', 'created_at'))
    if not rows:
        return
    confirmed = [ack for ack, _ in rows if ack >= 0]
    if confirmed:
        status = ACK_STATUS[max(confirmed)]
        if DELIVERY_RANK.get(status, 0) < DELIVERY_RANK.get(message.delivery_status, -1):
            status = message.delivery_status  # never downgrade
        if max(confirmed) >= 1:
            message.send_state = 'submitted'
            message.last_error = ''
        message.delivered_at = message.delivered_at or min((at for ack, at in rows if ack >= 2), default=None)
        message.read_at = message.read_at or min((at for ack, at in rows if ack >= 3), default=None)
    else:
        status = 'failed'  # only failures were reported, never a send confirmation
        message.last_error = ACK_FAILURES.get(min(ack for ack, _ in rows), ACK_FAILURES[-1])
    message.delivery_status = status
    message.save(update_fields=['delivery_status', 'send_state', 'last_error', 'delivered_at', 'read_at'])


def apply_client_receipt(message, status, when=None):
    """Website chat receipts confirmed by the visitor's widget. Monotonic: delivered -> read."""
    when = when or timezone.now()
    if DELIVERY_RANK.get(status, -1) <= DELIVERY_RANK.get(message.delivery_status, -1):
        return False
    message.delivery_status = status
    message.delivered_at = message.delivered_at or when
    if status == 'read':
        message.read_at = message.read_at or when
    message.save(update_fields=['delivery_status', 'delivered_at', 'read_at'])
    return True


@transaction.atomic
def record_inbound(business_id, connection_id, contact_id, provider_id, content, timestamp=None):
    if not isinstance(provider_id, str) or not provider_id or len(provider_id) > 255:
        raise ValidationError('Invalid message identifier.')
    connection = Connection.objects.select_for_update().get(
        id=connection_id, business_id=business_id, removed_at__isnull=True,
    )
    contact = Contact.objects.get(id=contact_id, business_id=business_id)
    channel_for(connection).validate(contact)
    from apps.ai_engine.models import AISettings
    automatic = connection.supports_auto_reply and AISettings.objects.filter(business_id=business_id, enabled=True).exists()
    # Website guests get their conversation (and mode) when the session is created.
    auto_start = connection.channel == 'whatsapp' and automatic
    conversation, conversation_created = Conversation.objects.get_or_create(
        business_id=business_id, connection=connection, contact=contact,
        defaults={'reply_mode': 'ai' if auto_start else 'manual'},
    )
    if conversation_created:
        logger.info('conversation.created conversation=%s channel=%s reply_mode=%s',
                    conversation.id, connection.channel, conversation.reply_mode)
    conversation = Conversation.objects.select_for_update().get(id=conversation.id, business_id=business_id)
    message, created = Message.objects.get_or_create(
        connection=connection,
        provider_id=provider_id,
        defaults={
            'business_id': business_id,
            'conversation': conversation,
            'direction': 'inbound',
            'sender_type': 'customer',
            'content': content,
            'provider_timestamp': timestamp,
            'delivery_status': 'received',
        },
    )
    if message.conversation_id != conversation.id or message.direction != 'inbound':
        raise ValidationError('This message identifier is already in use.')
    if not created and channel_for(connection).local_delivery and message.content != content:
        raise ValidationError('This message identifier was already used for different content.')
    if created:
        conversation.last_message = message
        conversation.last_activity_at = message.created_at
        conversation.unread_count += 1
        conversation.status = 'open'
        fields = ['last_message', 'last_activity_at', 'unread_count', 'status']
        mode = resumed_reply_mode(conversation, content) if automatic and not conversation_created else None
        if mode:
            logger.info('conversation.reply_mode_resumed conversation=%s from=%s to=%s',
                        conversation.id, conversation.reply_mode, mode)
            conversation.reply_mode = mode
            conversation.handoff_at = None
            fields += ['reply_mode', 'handoff_at']
        conversation.save(update_fields=fields)
        publish(business_id, 'message.created', conversation.id)
        from apps.ai_engine.tasks import schedule_reply
        schedule_reply(message, conversation)
    return message, created


@transaction.atomic
def process_event(business_id, event_id):
    item = WebhookEvent.objects.filter(id=event_id, business_id=business_id, connection__business_id=business_id, processed_at__isnull=True).first()
    if not item:
        return
    connection = Connection.objects.select_for_update().get(id=item.connection_id, business_id=business_id)
    first = WebhookEvent.objects.filter(connection=connection, processed_at__isnull=True, attempts__lt=5).order_by('created_at', 'id').first()
    if not first or first.id != item.id:
        return
    item = WebhookEvent.objects.select_for_update().get(id=item.id)
    if item.processed_at:
        return
    if connection.removed_at:
        item.processed_at = timezone.now()
        item.save(update_fields=['processed_at'])
        return
    payload = json.loads(decrypt(item.payload_encrypted))
    event = payload['event']
    if event in ('onmessage', 'unreadmessages'):
        contact, _ = Contact.objects.select_for_update().get_or_create(
            business_id=business_id, whatsapp_id=payload['from'], defaults={'display_name': payload.get('name', '')},
        )
        fields = []
        phone = payload.get('phone', '')
        if phone and not contact.mobile_encrypted:
            contact.mobile_encrypted = mobile_cipher().encrypt(phone.encode()).decode()
            fields.append('mobile_encrypted')
        if not contact.display_name and payload.get('name'):
            contact.display_name = payload['name']
            fields.append('display_name')
        if fields:
            contact.save(update_fields=[*fields, 'updated_at'])
        record_inbound(business_id, connection.id, contact.id, payload['id'], payload['body'], provider_time(payload.get('timestamp')))
    elif event == 'onack':
        DeliveryEvent.objects.get_or_create(business_id=business_id, connection=connection, provider_id=payload['id'], ack=payload['ack'])
        message = Message.objects.select_for_update().filter(business_id=business_id, connection=connection, provider_id=payload['id'], direction='outbound').first()
        if message:
            reconcile_delivery(message)
            publish(business_id, 'message.updated', message.conversation_id)
    else:
        if event == 'status-find' and payload.get('status'):
            from apps.whatsapp.services import record_status_event
            record_status_event(connection, payload['status'])
        # Fetch authoritative current provider state instead of applying stale status/QR events.
        from apps.whatsapp.tasks import connection_operation
        transaction.on_commit(lambda: enqueue(connection_operation, business_id, connection.id, 'sync'))
    item.processed_at = timezone.now()
    item.error = ''
    item.attempts += 1
    item.save()


@transaction.atomic
def queue_message(business_id, conversation_id, client_id, content, *, automated=False, sender_type=None):
    conversation = Conversation.objects.select_for_update(of=('self',)).select_related('connection', 'contact').get(id=conversation_id, business_id=business_id, connection__business_id=business_id, contact__business_id=business_id)
    old = Message.objects.filter(conversation=conversation, client_id=client_id).first()
    if old:
        if old.content != content:
            raise ValidationError('This request ID was already used for different content.')
        return old, False
    adapter = channel_for(conversation.connection)
    adapter.validate(conversation.contact)
    if conversation.connection.simulated and not conversation.connection.supports_auto_reply:
        raise ValidationError('Simulation is disabled.')
    if not adapter.local_delivery and not conversation.connection.whatsapp_ready:
        raise ValidationError('WhatsApp not connected.')
    if conversation.connection.state != 'connected' or conversation.connection.removed_at or conversation.connection.desired_state != 'open':
        raise ValidationError('The channel connection is unavailable.')
    if conversation.status != 'open':
        raise ValidationError('Reopen the conversation before sending.')
    item = Message.objects.create(
        business_id=business_id, connection=conversation.connection, conversation=conversation,
        client_id=client_id, direction='outbound', content=content, automated=automated,
        sender_type='ai' if automated else sender_type or 'agent',
        send_state='submitted' if adapter.local_delivery else 'queued',
        delivery_status='simulated' if conversation.connection.simulated else 'sent' if adapter.local_delivery else 'pending',
    )
    conversation.last_message = item
    conversation.last_activity_at = item.created_at
    conversation.save(update_fields=['last_message', 'last_activity_at'])
    if not adapter.local_delivery:
        from .tasks import dispatch_conversation
        transaction.on_commit(lambda: enqueue(dispatch_conversation, business_id, conversation.id))
    publish(business_id, 'message.created', conversation.id)
    return item, True


def dispatch(business_id, conversation_id):
    with transaction.atomic():
        conversation = Conversation.objects.select_for_update(of=('self',)).select_related('connection', 'contact').filter(id=conversation_id, business_id=business_id, contact__business_id=business_id, connection__business_id=business_id).first()
        if not conversation or conversation.connection.removed_at:
            return
        adapter = channel_for(conversation.connection)
        if adapter.local_delivery:
            return
        # A claimed send must serialize later messages. An UNKNOWN result must never
        # be retried automatically, but it must not permanently block newer sends.
        if conversation.messages.filter(direction='outbound', send_state='sending').exists():
            return
        pending = conversation.messages.filter(direction='outbound', send_state='queued').order_by('created_at', 'id').first()
        if not pending or pending.next_attempt_at > timezone.now():
            return
        if not conversation.connection.whatsapp_ready:
            # Left queued (recovery retries) but made visible instead of failing silently.
            logger.warning('whatsapp.send deferred conversation=%s message=%s reason=connection_not_ready',
                           conversation_id, pending.id)
            if not pending.last_error:
                pending.last_error = 'WhatsApp connection is not ready; the message will send after reconnecting.'
                pending.save(update_fields=['last_error'])
            return
        if pending.automated:
            from apps.ai_engine.models import AISettings
            enabled = AISettings.objects.filter(business_id=business_id, enabled=True).exists()
            if not enabled or not conversation.connection.ai_enabled or conversation.reply_mode == 'manual' or conversation.status != 'open':
                logger.info('whatsapp.send cancelled_automated conversation=%s message=%s workspace_ai=%s '
                            'connection_ai=%s reply_mode=%s status=%s', conversation_id, pending.id, enabled,
                            conversation.connection.ai_enabled, conversation.reply_mode, conversation.status)
                pending.send_state = 'cancelled'
                pending.last_error = 'Automatic reply cancelled before submission.'
                pending.save(update_fields=['send_state', 'last_error'])
                publish(business_id, 'message.updated', conversation_id)
                from .tasks import dispatch_conversation
                transaction.on_commit(lambda: enqueue(dispatch_conversation, business_id, conversation_id))
                return
        pending.send_state = 'sending'
        pending.sending_at = timezone.now()
        pending.attempts += 1
        pending.save()
        message_pk = pending.id
        resolve = adapter.resolver(conversation.connection)
        send = adapter.sender(conversation.connection)
        recipient = conversation.contact.whatsapp_id
        contact_pk = conversation.contact_id
        text = pending.content
        attachment = MessageAttachment.objects.filter(message_id=pending.id, business_id=business_id).first()
        send_file = adapter.file_sender(conversation.connection) if attachment else None
    # Claim is committed BEFORE the non-idempotent provider call.
    try:
        # The optional LID-to-phone lookup must never block a valid native-LID
        # send. Some WPPConnect sessions can send to @lid while the lookup fails.
        routable_recipient = recipient
        if recipient.endswith('@lid'):
            try:
                routable_recipient = resolve(recipient)
            except ProviderError:
                logger.warning('Optional WPPConnect contact lookup unavailable; using native LID.')
        if routable_recipient != recipient:
            phone = '+' + routable_recipient.removesuffix('@c.us')
            with transaction.atomic():
                contact = Contact.objects.select_for_update().get(
                    pk=contact_pk, business_id=business_id,
                )
                if not contact.mobile_encrypted:
                    contact.mobile_encrypted = mobile_cipher().encrypt(phone.encode()).decode()
                    contact.save(update_fields=['mobile_encrypted', 'updated_at'])
        # Keep the provider's original LID as the delivery address. Resolving it
        # is only for CRM display: this WPPConnect runtime crashes while converting
        # a c.us recipient back to LID, but accepts its native LID directly.
        if attachment:
            from .attachments import read as read_attachment
            try:
                data = read_attachment(attachment)
            except ValidationError:
                raise SafeToRetry('Attachment storage unavailable before submission.') from None
            identifier = send_file(recipient, data, attachment.content_type, attachment.filename, attachment.message.content
                                   if not attachment.message.content.startswith('📎') else '')
        else:
            identifier = send(recipient, text)
        state, error = 'submitted', ''
    except SafeToRetry:
        identifier, state, error = None, 'queued' if pending.attempts < 3 else 'failed', 'Provider unreachable before submission.'
    except ProviderError:
        identifier, state, error = None, 'unknown', 'Delivery outcome unknown. Verify in WhatsApp before resolving.'
    except Exception as exc:
        # Unexpected bug after the claim: never auto-retry a possibly-sent message.
        logger.error('whatsapp.send unexpected_error conversation=%s message=%s error=%s',
                     conversation_id, message_pk, type(exc).__name__)
        identifier, state, error = None, 'unknown', 'Unexpected send error. Verify in WhatsApp before resolving.'
    log = logger.info if state == 'submitted' else logger.warning
    log('whatsapp.send result conversation=%s message=%s state=%s attempt=%s', conversation_id, message_pk, state, pending.attempts)
    with transaction.atomic():
        # Ingress ACK processing takes this same lock before matching provider IDs.
        # Either the ACK commits first, or it sees the ID after this commit.
        Connection.objects.select_for_update().get(pk=conversation.connection_id, business_id=business_id)
        item = Message.objects.select_for_update().get(id=message_pk, business_id=business_id)
        item.provider_id = identifier
        item.send_state = state
        item.last_error = error
        item.next_attempt_at = timezone.now() + timedelta(seconds=15 * item.attempts)
        item.save()
        if identifier:
            reconcile_delivery(item)
        publish(business_id, 'message.updated', conversation_id)
    if state in ('submitted', 'failed', 'unknown'):
        # An ambiguous send must not be retried, but it must not strand the
        # next queued message until periodic recovery runs.
        from .tasks import dispatch_conversation
        enqueue(dispatch_conversation, business_id, conversation_id)
