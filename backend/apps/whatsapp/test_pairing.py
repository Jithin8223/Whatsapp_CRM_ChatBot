"""Pairing, QR delivery, reconnection and multi-session isolation (WPPConnect mocked)."""
import base64
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.conversations.models import WebhookEvent
from apps.conversations.services import process_event
from apps.tenants.models import AuditEvent, Business, Membership
from .crypto import encrypt
from .models import Connection, ConnectionOperation
from .providers import ProviderError, SafeToRetry
from .services import LOGGED_OUT, RESTORING, SCAN_PROMPT, TIMED_OUT, operate_connection

SECRET = 'pairing-test-secret'
QR_ONE = 'data:image/png;base64,' + base64.b64encode(b'qr-one').decode()
QR_TWO = 'data:image/png;base64,' + base64.b64encode(b'qr-two').decode()


@override_settings(WPPCONNECT_ENABLED=True, WPPCONNECT_WEBHOOK_SECRET=SECRET)
@patch('apps.whatsapp.services.provider_for')
class PairingTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='pairing-admin')
        self.business = Business.objects.create(name='Pairing workspace')
        Membership.objects.create(business=self.business, user=self.admin, role='admin')
        self.connection = Connection.objects.create(business=self.business, name='Sales')
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.base = f'/api/workspaces/{self.business.id}/connections/'
        self.auth = 'Basic ' + base64.b64encode(f'wppconnect:{SECRET}'.encode()).decode()

    def webhook(self, connection, event, **data):
        return APIClient().post('/internal/wppconnect/', {'session': connection.session, 'event': event, **data},
                                format='json', HTTP_AUTHORIZATION=self.auth)

    def process_all(self):
        for event in WebhookEvent.objects.filter(processed_at__isnull=True).order_by('created_at'):
            process_event(event.business_id, event.id)

    def qr(self, connection=None, business=None):
        business = business or self.business
        connection = connection or self.connection
        return self.client.get(f'/api/workspaces/{business.id}/connections/{connection.id}/qr/')

    # -- 1. QR generation ------------------------------------------------------------
    def test_start_then_qr_is_shown_promptly_with_progress(self, factory):
        factory.return_value.generate_token.return_value = 'private-session-token'
        operate_connection(self.business.id, self.connection.id, 'start')
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.state, 'connecting')
        self.assertIn('Starting', self.connection.status_detail)
        self.assertIsNotNone(self.connection.state_changed_at)

        # The QR endpoint reads the provider directly while pairing (no 15 s wait).
        factory.return_value.status.return_value = {'state': 'qr', 'qrcode': QR_ONE}
        Connection.objects.filter(pk=self.connection.pk).update(checked_at=timezone.now() - timedelta(seconds=10))
        data = self.qr().data
        self.assertEqual(data['qrcode'], QR_ONE)
        self.assertEqual(data['state'], 'qr')
        self.assertEqual(data['detail'], SCAN_PROMPT)
        self.assertIn('no-store', self.qr()['Cache-Control'])
        self.assertNotIn('private-session-token', str(data))

    def test_unchanged_qr_stays_valid_while_provider_still_shows_it(self, factory):
        self.connection.token_encrypted = encrypt('token')
        self.connection.save(update_fields=['token_encrypted'])
        factory.return_value.status.return_value = {'state': 'qr', 'qrcode': QR_ONE}
        operate_connection(self.business.id, self.connection.id, 'sync')
        Connection.objects.filter(pk=self.connection.pk).update(qr_expires_at=timezone.now() - timedelta(seconds=1))
        operate_connection(self.business.id, self.connection.id, 'sync')
        self.connection.refresh_from_db()
        self.assertGreater(self.connection.qr_expires_at, timezone.now())
        factory.return_value.status.return_value = {'state': 'qr', 'qrcode': QR_TWO}
        operate_connection(self.business.id, self.connection.id, 'sync')
        Connection.objects.filter(pk=self.connection.pk).update(checked_at=timezone.now())
        self.assertEqual(self.qr().data['qrcode'], QR_TWO)

    def test_repeated_qr_and_status_events_are_not_dropped_as_duplicates(self, factory):
        self.connection.token_encrypted = encrypt('token')
        self.connection.save(update_fields=['token_encrypted'])
        with patch('apps.conversations.services.event_time_bucket', side_effect=[66, 66, 73]):
            self.assertFalse(self.webhook(self.connection, 'qrcode', qrcode=QR_ONE).data['duplicate'])
            self.assertTrue(self.webhook(self.connection, 'qrcode', qrcode=QR_ONE).data['duplicate'])  # provider retry
            self.assertFalse(self.webhook(self.connection, 'qrcode', qrcode=QR_TWO).data['duplicate'])  # later QR
        # The webhook never stores QR image data.
        for event in WebhookEvent.objects.all():
            self.assertNotIn('qr-one', event.payload_encrypted)

    def test_status_events_report_pairing_progress_and_timeout(self, factory):
        self.connection.token_encrypted = encrypt('token')
        self.connection.state = 'qr'
        self.connection.save(update_fields=['token_encrypted', 'state'])
        factory.return_value.status.return_value = {'state': 'qr', 'qrcode': QR_ONE}
        self.webhook(self.connection, 'status-find', status='qrReadSuccess')
        with self.captureOnCommitCallbacks(execute=False):
            self.process_all()
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.status_detail, 'QR code scanned. Signing in…')
        with patch('apps.conversations.services.event_time_bucket', return_value=333):
            self.webhook(self.connection, 'status-find', status='autocloseCalled')
        with self.captureOnCommitCallbacks(execute=False):
            self.process_all()
        factory.return_value.status.return_value = {'state': 'disconnected'}
        operate_connection(self.business.id, self.connection.id, 'sync')
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.state, 'disconnected')
        self.assertEqual(self.connection.status_detail, TIMED_OUT)
        self.assertEqual(self.qr().data['detail'], TIMED_OUT)
        self.assertFalse(ConnectionOperation.objects.exists())  # no automatic retry loop after a timeout

    def test_browser_that_stops_before_qr_is_reported(self, factory):
        self.connection.token_encrypted = encrypt('token')
        self.connection.save(update_fields=['token_encrypted'])
        factory.return_value.start.return_value = {}
        operate_connection(self.business.id, self.connection.id, 'start')
        factory.return_value.status.return_value = {'state': 'disconnected'}
        operate_connection(self.business.id, self.connection.id, 'sync')
        self.connection.refresh_from_db()
        self.assertIn('stopped before showing a QR', self.connection.status_detail)

    def test_start_errors_are_specific_and_sanitized(self, factory):
        factory.return_value.generate_token.side_effect = SafeToRetry('upstream secret detail')
        operate_connection(self.business.id, self.connection.id, 'start')
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.state, 'error')
        self.assertIn('unreachable', self.connection.last_error)
        self.assertNotIn('secret', self.connection.last_error)
        factory.return_value.generate_token.side_effect = None
        factory.return_value.generate_token.return_value = 'token'
        factory.return_value.start.side_effect = ProviderError('raw')
        operate_connection(self.business.id, self.connection.id, 'start')
        self.connection.refresh_from_db()
        self.assertIn('could not start', self.connection.last_error)

    def test_concurrent_pairing_operations_are_rejected(self, factory):
        with patch('apps.whatsapp.outbox.enqueue_operation'):
            self.assertEqual(self.client.post(self.base + f'{self.connection.id}/start/').status_code, 202)
            second = self.client.post(self.base + f'{self.connection.id}/reconnect/')
        self.assertEqual(second.status_code, 400)
        self.assertEqual(ConnectionOperation.objects.filter(completed_at__isnull=True).count(), 1)

    # -- 2. Reconnection -------------------------------------------------------------
    def test_dropped_session_is_restored_once_without_loops(self, factory):
        self.connection.token_encrypted = encrypt('token')
        self.connection.state = 'connected'
        self.connection.desired_state = 'open'
        self.connection.checked_at = timezone.now() - timedelta(minutes=5)
        self.connection.save()
        factory.return_value.status.return_value = {'state': 'disconnected'}
        with patch('apps.whatsapp.outbox.enqueue_operation'):
            operate_connection(self.business.id, self.connection.id, 'sync')
            operate_connection(self.business.id, self.connection.id, 'sync')
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.status_detail, RESTORING)
        operation = ConnectionOperation.objects.get()
        self.assertEqual(operation.action, 'start')
        self.assertTrue(AuditEvent.objects.filter(action='connection.start', actor=None).exists())
        # Completed restore that still fails does not queue another one.
        operation.completed_at = timezone.now()
        operation.save()
        with patch('apps.whatsapp.outbox.enqueue_operation'):
            operate_connection(self.business.id, self.connection.id, 'sync')
        self.assertEqual(ConnectionOperation.objects.count(), 1)

    def test_logged_out_or_admin_disconnected_sessions_are_not_restored(self, factory):
        self.connection.token_encrypted = encrypt('token')
        self.connection.state = 'connected'
        self.connection.status_detail = LOGGED_OUT
        self.connection.save()
        factory.return_value.status.return_value = {'state': 'disconnected'}
        operate_connection(self.business.id, self.connection.id, 'sync')
        self.connection.state = 'connected'
        self.connection.status_detail = ''
        self.connection.desired_state = 'closed'
        self.connection.save()
        operate_connection(self.business.id, self.connection.id, 'sync')
        self.assertFalse(ConnectionOperation.objects.exists())

    def test_one_failed_status_poll_keeps_a_verified_session_ready(self, factory):
        self.connection.token_encrypted = encrypt('token')
        self.connection.state = 'connected'
        self.connection.desired_state = 'open'
        self.connection.checked_at = timezone.now() - timedelta(seconds=20)
        self.connection.save()
        factory.return_value.status.side_effect = ProviderError('timeout')
        operate_connection(self.business.id, self.connection.id, 'sync')
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.state, 'connected')
        self.assertTrue(self.connection.whatsapp_ready)
        self.assertIn('Could not read', self.connection.last_error)
        Connection.objects.filter(pk=self.connection.pk).update(checked_at=timezone.now() - timedelta(minutes=3))
        operate_connection(self.business.id, self.connection.id, 'sync')
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.state, 'error')

    # -- 3. Multiple isolated sessions -----------------------------------------------
    def test_multiple_sessions_and_workspaces_stay_isolated(self, factory):
        other_business = Business.objects.create(name='Other workspace')
        Membership.objects.create(business=other_business, user=self.admin, role='admin')
        second = Connection.objects.create(business=self.business, name='Support', token_encrypted=encrypt('t2'), state='qr')
        foreign = Connection.objects.create(business=other_business, name='Foreign', token_encrypted=encrypt('t3'), state='qr')
        self.assertEqual(len({self.connection.session, second.session, foreign.session}), 3)
        self.webhook(second, 'status-find', status='qrReadSuccess')
        self.webhook(foreign, 'status-find', status='qrReadFail')
        events = {event.connection_id: event for event in WebhookEvent.objects.all()}
        self.assertEqual(events[second.id].business_id, self.business.id)
        self.assertEqual(events[foreign.id].business_id, other_business.id)
        with self.captureOnCommitCallbacks(execute=False):
            self.process_all()
        second.refresh_from_db()
        foreign.refresh_from_db()
        self.connection.refresh_from_db()
        self.assertIn('scanned', second.status_detail)
        self.assertIn('could not be read', foreign.status_detail)
        self.assertEqual(self.connection.status_detail, '')
        # A workspace cannot read another workspace's QR, even with a valid connection id.
        self.assertEqual(self.qr(foreign, self.business).status_code, 404)
        self.assertEqual(self.webhook(Connection(session='crm_unknown'), 'qrcode').status_code, 404)
