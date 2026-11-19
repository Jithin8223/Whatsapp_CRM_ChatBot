import base64
import binascii
import hashlib
import hmac
import logging
import re
from django.conf import settings
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.whatsapp.models import Connection
from apps.whatsapp.providers import message_id
from .services import persist_event

logger = logging.getLogger(__name__)

UNSUPPORTED_BODY = '[Unsupported message type]'
# Message types whose body is the customer's text (list/button replies carry the chosen label).
TEXT_TYPES = (None, 'chat', 'list_response', 'buttons_response', 'template_button_reply')
# System, protocol and not-yet-decrypted events are not customer messages and must never
# trigger an automatic reply.
IGNORED_TYPES = frozenset((
    'ciphertext', 'e2e_notification', 'notification', 'notification_template', 'gp2', 'protocol',
    'revoked', 'call_log', 'broadcast_notification', 'reaction', 'keep_in_chat', 'pin', 'pinned_message',
    'debug', 'unknown', 'newsletter_notification',
))


def synthetic_message_id(sender, timestamp, body):
    """Stable dedup key for messages whose WhatsApp key was not serialized.

    WA-JS serializes ``id`` as ``msg.id._serialized``; some WhatsApp Web builds leave
    that undefined, so the JSON webhook arrives without ``id``. WPPConnect retries
    resend the same payload, so a hash of sender, provider timestamp and body
    deduplicates them without inventing a provider message ID.
    """
    digest = hashlib.sha256(f'{sender}|{timestamp}|{body}'.encode()).hexdigest()[:40]
    return f'nokey:{digest}'


def whatsapp_address(value):
    address = message_id(value)
    if not address and isinstance(value, dict):
        user, server = value.get('user'), value.get('server')
        if isinstance(user, str) and isinstance(server, str):
            address = f'{user}@{server}'
    return re.sub(r'(?<=\d)(?::\d+)?@(c\.us|s\.whatsapp\.net)$', '@c.us', address)


def sender_phone(address, contact):
    candidates = (address, whatsapp_address(contact.get('id')), whatsapp_address(contact.get('phoneNumber')))
    for candidate in candidates:
        matched = re.fullmatch(r'([1-9]\d{7,14})@c\.us', candidate or '')
        if matched:
            return '+' + matched[1]
    return ''


class WebhookView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = []

    def post(self, request):
        expected = settings.WPPCONNECT_WEBHOOK_SECRET
        try:
            scheme, raw = request.headers.get('Authorization', '').split(' ', 1)
            decoded = base64.b64decode(raw, validate=True).decode()
            user, secret = decoded.split(':', 1)
            valid = scheme.lower() == 'basic' and user == 'wppconnect' and bool(expected) and hmac.compare_digest(secret, expected)
        except (ValueError, UnicodeError, binascii.Error):
            valid = False
        if not valid:
            return Response({'detail': 'Invalid webhook credentials.'}, status=401)
        # Authentication precedes JSON parsing. Ingress middleware limits body size.
        data = request.data
        if not isinstance(data, dict):
            raise serializers.ValidationError('Expected an event object.')
        session = data.get('session')
        if not isinstance(session, str) or len(session) > 64:
            raise serializers.ValidationError('Invalid session.')
        connection = Connection.objects.filter(provider='wppconnect', session=session, removed_at__isnull=True).first()
        if connection is None:
            return Response({'detail': 'Unknown session.'}, status=404)
        if not settings.WPPCONNECT_ENABLED:
            return Response({'accepted': False, 'reason': 'integration_disabled'})
        event = data.get('event')
        if event not in ('onmessage', 'unreadmessages', 'onack', 'qrcode', 'status-find', 'closesession'):
            return Response({'accepted': False, 'reason': 'unsupported_event'})
        payload = {'event': event}
        if event in ('onmessage', 'unreadmessages'):
            key = data['id'] if isinstance(data.get('id'), dict) else {}
            identifier = message_id(data.get('id'))
            if (data.get('fromMe') is True or data.get('isGroupMsg') is True
                    or key.get('fromMe') is True or identifier.startswith('true_')):
                return Response({'accepted': False, 'reason': 'ignored_message'})
            kind = data.get('type')
            if data.get('isNotification') is True or kind in IGNORED_TYPES:
                logger.info('wppconnect.webhook ignored event=%s type=%s connection=%s', event, kind, connection.id)
                return Response({'accepted': False, 'reason': 'ignored_message_type'})
            contact = data['sender'] if isinstance(data.get('sender'), dict) else {}
            addresses = [whatsapp_address(value) for value in (data.get('from'), key.get('remote'), data.get('chatId'))]
            if any(address.endswith(('@g.us', '@broadcast', '@newsletter')) for address in addresses):
                return Response({'accepted': False, 'reason': 'ignored_message'})
            addresses.append(whatsapp_address(contact.get('id')))
            sender = next((address for address in addresses if re.fullmatch(r'[0-9]{5,25}@(c\.us|lid)', address)), '')
            body = data.get('body', '') if kind in TEXT_TYPES else UNSUPPORTED_BODY
            if body is None:
                body = ''
            if not isinstance(body, str) or len(body) > 10000:
                raise serializers.ValidationError('Invalid message content.')
            timestamp = data.get('timestamp', data.get('t'))
            if sender and not identifier and type(timestamp) in (int, float) and timestamp > 0:
                identifier = synthetic_message_id(sender, timestamp, body)
                logger.info('wppconnect.webhook message without serialized id; using synthetic key '
                            '(event=%s, id_type=%s, connection=%s)', event, type(data.get('id')).__name__, connection.id)
            if not sender or not identifier:
                # Structure only: never log identifiers, numbers or message content.
                logger.warning('wppconnect.webhook direct message rejected (event=%s, valid_sender=%s, has_id=%s, '
                               'id_type=%s, has_timestamp=%s, type=%s, keys=%s).',
                               event, bool(sender), bool(identifier), type(data.get('id')).__name__,
                               timestamp is not None, kind, sorted(data)[:40])
                raise serializers.ValidationError('Invalid direct message identifiers.')
            name = contact.get('pushname') or contact.get('name') or ''
            payload.update({'id': identifier, 'from': sender, 'body': body,
                            'timestamp': timestamp,
                            'name': name[:120] if isinstance(name, str) else '', 'phone': sender_phone(sender, contact)})
        elif event == 'onack':
            identifier = message_id(data.get('id'))
            ack = data.get('ack')
            # WPPConnect AckType spans -7 (failures) to 5 (PEER); all are recorded.
            if not identifier or type(ack) is not int or not -7 <= ack <= 5:
                logger.warning('WPPConnect acknowledgement rejected (has_id=%s, valid_ack=%s).',
                               bool(identifier), type(ack) is int and -7 <= ack <= 5)
                raise serializers.ValidationError('Invalid delivery event.')
            payload.update({'id': identifier, 'ack': ack})
        else:
            payload['status'] = str(data.get('status', ''))[:80]
            # QR is fetched from the provider by a task and never stored in raw webhook records.
        created = persist_event(connection, payload)
        logger.info('wppconnect.webhook accepted event=%s duplicate=%s connection=%s', event, not created, connection.id)
        return Response({'accepted': True, 'duplicate': not created}, status=202)
