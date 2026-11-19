import logging
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from apps.notifications.events import publish
from .crypto import encrypt, decrypt
from .models import Connection
from .providers import provider_for, ProviderError, SafeToRetry

logger = logging.getLogger(__name__)

QR_TTL = timedelta(seconds=45)
# A healthy session survives one failed status poll; only sustained failure is an error.
SYNC_GRACE = timedelta(seconds=90)
SLOW_START = timedelta(seconds=120)

LOGGED_OUT = 'WhatsApp logged this device out. Pair again by scanning a new QR code.'
TIMED_OUT = 'Pairing timed out because the QR code was not scanned in time. Click Reconnect for a fresh code.'
SCAN_PROMPT = 'Scan the QR code in WhatsApp > Settings > Linked devices > Link a device.'
RESTORING = 'The connection dropped. Restoring the saved WhatsApp session automatically…'
STOPPED_EARLY = 'The WhatsApp browser stopped before showing a QR code. Click Reconnect to try again.'
SLOW = 'Still starting the WhatsApp session. After a server restart this can take a few minutes.'

# WPPConnect status-find values -> admin-facing progress. Unknown values are ignored.
STATUS_DETAILS = {
    'initBrowser': 'Starting the WhatsApp browser session…',
    'openBrowser': 'Starting the WhatsApp browser session…',
    'connectBrowserWs': 'Starting the WhatsApp browser session…',
    'initWhatsapp': 'Loading WhatsApp Web…',
    'successPageWhatsapp': 'Loading WhatsApp Web…',
    'waitForLogin': 'Loading WhatsApp Web…',
    'notLogged': SCAN_PROMPT,
    'qrReadSuccess': 'QR code scanned. Signing in…',
    'qrReadFail': 'The QR code could not be read. Request a fresh code and try again.',
    'isLogged': 'Signed in. Loading chats…',
    'waitChat': 'Signed in. Loading chats…',
    'successChat': 'Signed in. Loading chats…',
    'chatsAvailable': 'Signed in. Loading chats…',
    'inChat': 'Signed in. Loading chats…',
    'autocloseCalled': TIMED_OUT,
    'browserClose': 'The WhatsApp browser closed unexpectedly. Click Reconnect.',
    'noOpenBrowser': 'The WhatsApp browser could not start. Retry in a few seconds.',
    'desconnectedMobile': LOGGED_OUT,
    'disconnectedMobile': LOGGED_OUT,
    'deleteToken': LOGGED_OUT,
    'erroPageWhatsapp': 'WhatsApp Web failed to load. Retry in a moment.',
    'phoneNotConnected': 'The phone is offline. Make sure it has internet access.',
    'deviceNotConnected': 'The phone is offline. Make sure it has internet access.',
    'serverClose': 'Lost connection to WhatsApp servers. Retrying…',
    'serverWssNotConnected': 'Lost connection to WhatsApp servers. Retrying…',
}
ERRORS = {
    'unreachable': 'The WhatsApp service is unreachable. It may still be starting; retry in a few seconds.',
    'start': 'WhatsApp could not start this session. Retry in a few seconds.',
    'sync': 'Could not read the session status from the WhatsApp service.',
    'disconnect': 'WhatsApp could not close this session. Retry.',
    'remove': 'WhatsApp could not remove this session. Retry.',
}


def record_status_event(connection, status):
    """Store pairing progress from a WPPConnect status-find webhook (already locked)."""
    detail = STATUS_DETAILS.get(status)
    if detail and detail != connection.status_detail:
        connection.status_detail = detail
        connection.save(update_fields=['status_detail', 'updated_at'])
        publish(connection.business_id, 'connection.updated', connection.id)


def _set_state(connection, state, now):
    if state != connection.state:
        connection.state = state
        connection.state_changed_at = now


def apply_status(connection, data):
    previous = (connection.state, connection.qr_encrypted, connection.last_error, connection.status_detail)
    previous_state = connection.state
    now = timezone.now()
    _set_state(connection, data['state'], now)
    qr = data.get('qrcode')
    if isinstance(qr, str) and qr and len(qr) < 200000 and connection.state != 'connected':
        if not qr.startswith('data:image/png;base64,'):
            qr = 'data:image/png;base64,' + qr
        if qr != decrypt(connection.qr_encrypted):
            connection.qr_encrypted = encrypt(qr)
        # The provider still shows this code, so it is still scannable.
        connection.qr_expires_at = now + QR_TTL
        _set_state(connection, 'qr', now)
    else:
        connection.qr_encrypted = ''
        connection.qr_expires_at = None
    connection.checked_at = now
    connection.last_error = ''
    restore = False
    if connection.state == 'connected':
        connection.status_detail = ''
    elif connection.state == 'qr':
        if connection.status_detail != 'QR code scanned. Signing in…':
            connection.status_detail = SCAN_PROMPT
    elif connection.state == 'disconnected':
        if previous_state == 'connected' and connection.desired_state == 'open' and connection.status_detail != LOGGED_OUT:
            restore = True
            connection.status_detail = RESTORING
        elif previous_state == 'connecting' and connection.status_detail not in (TIMED_OUT, LOGGED_OUT):
            connection.status_detail = STOPPED_EARLY
    elif connection.state == 'connecting' and connection.state_changed_at and now - connection.state_changed_at > SLOW_START:
        connection.status_detail = SLOW
    connection.save()
    if restore:
        # One start attempt per connected->disconnected transition: a failed restore
        # leaves the state 'disconnected'/'qr', so this never loops on a healthy session.
        from rest_framework.exceptions import ValidationError
        from .outbox import queue_operation
        try:
            queue_operation(connection, 'start', None)
            logger.warning('whatsapp.connection restore_queued connection=%s', connection.id)
        except ValidationError:
            pass  # an operation is already pending
    current = (connection.state, connection.qr_encrypted, connection.last_error, connection.status_detail)
    if current != previous:
        if connection.state != previous_state:
            logger.info('whatsapp.connection state connection=%s from=%s to=%s', connection.id, previous_state, connection.state)
        publish(connection.business_id, 'connection.updated', connection.id)


@transaction.atomic
def operate_connection(business_id, connection_id, action):
    item = Connection.objects.select_for_update().get(id=connection_id, business_id=business_id)
    if item.removed_at:
        return
    adapter = provider_for(item)
    try:
        if not item.token_encrypted:
            item.token_encrypted = encrypt(adapter.generate_token())
            item.save(update_fields=['token_encrypted'])
        if action == 'start':
            adapter.start()
            _set_state(item, 'connecting', timezone.now())
            item.last_error = ''
            item.status_detail = 'Starting the WhatsApp browser session…'
            item.save()
        elif action in ('disconnect', 'remove'):
            adapter.close(logout=action == 'remove')
            _set_state(item, 'disconnected', timezone.now())
            item.qr_encrypted = ''
            item.qr_expires_at = None
            item.status_detail = ''
            if action == 'remove':
                item.removed_at = timezone.now()
                item.token_encrypted = ''
            item.last_error = ''
            item.save()
        else:
            apply_status(item, adapter.status())
            return
    except ProviderError as error:
        reason = 'unreachable' if isinstance(error, SafeToRetry) else action if action in ERRORS else 'sync'
        logger.warning('whatsapp.connection operation_failed connection=%s action=%s reason=%s', item.id, action, reason)
        item.last_error = ERRORS[reason]
        now = timezone.now()
        if action == 'sync' and item.state == 'connected' and item.checked_at and now - item.checked_at < SYNC_GRACE:
            # Keep a recently verified session usable; record the problem only.
            item.save(update_fields=['last_error', 'updated_at'])
        else:
            _set_state(item, 'error', now)
            item.qr_encrypted = ''
            item.qr_expires_at = None
            item.save()
    publish(item.business_id, 'connection.updated', item.id)
