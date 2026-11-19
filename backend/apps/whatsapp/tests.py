from unittest.mock import patch, Mock
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.tenants.models import Business, Membership
from .models import Connection
from .crypto import encrypt, decrypt
from .services import operate_connection
from .providers import WPPConnectProvider, AmbiguousSend, SafeToRetry, ProviderError
import httpx


@override_settings(WPPCONNECT_ENABLED=True)
class ProviderTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner')
        self.business = Business.objects.create(name='Shop')
        self.member = Membership.objects.create(user=self.user, business=self.business, role='admin')
        self.connection = Connection.objects.create(business=self.business, name='Sales')
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.base = f'/api/workspaces/{self.business.id}/connections/'

    def test_connection_creation_hides_credentials(self):
        result = self.client.post(self.base, {'name': 'Support'}, format='json')
        self.assertEqual(result.status_code, 201)
        self.assertNotIn('token_encrypted', result.data)
        self.assertNotIn('session', result.data)

    def test_deployment_status_reads_runtime_flag_and_enforces_workspace(self):
        url = self.base + 'deployment-status/'
        with self.settings(WPPCONNECT_ENABLED=False):
            self.assertEqual(self.client.get(url).data, {'enabled': False})
        self.assertEqual(self.client.get(url).data, {'enabled': True})
        other = Business.objects.create(name='Other')
        foreign_url = f'/api/workspaces/{other.id}/connections/deployment-status/'
        self.assertEqual(self.client.get(foreign_url).status_code, 404)
        self.assertEqual(APIClient().get(url).status_code, 401)

    @patch.object(WPPConnectProvider, 'request')
    def test_connected_requires_a_signed_in_device(self, request):
        adapter = WPPConnectProvider(self.connection)
        request.side_effect = [
            {'status': 'QRCODE', 'qrcode': 'example'},
            {'status': 'CONNECTED'}, {'response': {'phoneNumber': None}},
            {'status': 'CONNECTED'}, {'response': {'phoneNumber': {'_serialized': '12025550149@c.us'}}},
        ]
        self.assertEqual(adapter.status(), {'state': 'qr', 'qrcode': 'example'})
        self.assertEqual(request.call_count, 1)
        with self.assertRaises(ProviderError):
            adapter.status()
        self.assertEqual(adapter.status(), {'state': 'connected', 'qrcode': None})

    @override_settings(WPPCONNECT_ENABLED=False)
    @patch('apps.whatsapp.views.queue_operation')
    def test_disabled_integration_preserves_records_and_blocks_pairing(self, queue_operation):
        self.assertEqual(self.client.get(self.base).status_code, 200)
        self.assertEqual(self.client.get(f'{self.base}{self.connection.id}/qr/').status_code, 403)
        self.assertEqual(self.client.post(f'{self.base}{self.connection.id}/start/').status_code, 403)
        queue_operation.assert_not_called()
        self.assertTrue(Connection.objects.filter(pk=self.connection.pk).exists())

    @override_settings(CACHES={'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'connection-throttle-regression',
    }})
    @patch('apps.whatsapp.views.queue_operation')
    def test_reads_and_writes_have_separate_budgets(self, queue_operation):
        for _ in range(31):
            self.assertEqual(self.client.get(self.base).status_code, 200)
        for action in ('disconnect', 'remove'):
            result = self.client.post(f'{self.base}{self.connection.id}/{action}/')
            self.assertEqual(result.status_code, 202)
        for _ in range(28):
            self.assertEqual(self.client.post(f'{self.base}{self.connection.id}/start/').status_code, 202)
        result = self.client.post(f'{self.base}{self.connection.id}/remove/')
        self.assertEqual(result.status_code, 429)
        self.assertGreater(int(result['Retry-After']), 0)
        self.assertEqual(queue_operation.call_count, 30)
        self.assertEqual(self.client.get(self.base).status_code, 200)

    def test_agents_cannot_create_or_pair(self):
        self.member.role = 'agent'
        self.member.save()
        self.assertEqual(self.client.post(self.base, {'name': 'x'}).status_code, 403)
        self.assertEqual(self.client.post(f'{self.base}{self.connection.id}/start/').status_code, 403)

    def test_foreign_connection_is_hidden(self):
        other = Business.objects.create(name='Other')
        item = Connection.objects.create(business=other, name='Secret')
        self.assertEqual(self.client.get(f'{self.base}{item.id}/').status_code, 404)

    @patch('apps.whatsapp.services.provider_for')
    def test_pairing_and_reconnection_status(self, factory):
        factory.return_value.generate_token.return_value = 'provider-private-token'
        operate_connection(self.business.id, self.connection.id, 'start')
        self.connection.refresh_from_db()
        self.assertEqual(decrypt(self.connection.token_encrypted), 'provider-private-token')
        self.assertNotIn('provider-private-token', self.connection.token_encrypted)
        factory.return_value.status.return_value = {'state': 'qr', 'qrcode': 'data:image/png;base64,aGVsbG8='}
        operate_connection(self.business.id, self.connection.id, 'sync')
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.state, 'qr')
        self.assertTrue(self.client.get(f'{self.base}{self.connection.id}/qr/').data['qrcode'])
        factory.return_value.status.return_value = {'state': 'connected'}
        operate_connection(self.business.id, self.connection.id, 'sync')
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.state, 'connected')
        self.assertFalse(self.connection.qr_encrypted)
        operate_connection(self.business.id, self.connection.id, 'disconnect')
        factory.return_value.close.assert_called_with(logout=False)

    @patch('apps.whatsapp.services.provider_for')
    @patch('apps.whatsapp.services.publish')
    def test_status_sync_only_publishes_changes(self, publish, factory):
        self.connection.state = 'connected'
        self.connection.token_encrypted = encrypt('provider-private-token')
        self.connection.save(update_fields=['state', 'token_encrypted'])
        factory.return_value.status.return_value = {'state': 'connected'}
        operate_connection(self.business.id, self.connection.id, 'sync')
        self.connection.refresh_from_db()
        self.assertIsNotNone(self.connection.checked_at)
        publish.assert_not_called()
        factory.return_value.status.return_value = {'state': 'disconnected'}
        operate_connection(self.business.id, self.connection.id, 'sync')
        publish.assert_called_once_with(self.business.id, 'connection.updated', self.connection.id)

    @patch('apps.whatsapp.services.provider_for')
    def test_provider_error_is_sanitized(self, factory):
        factory.return_value.generate_token.side_effect = SafeToRetry('private upstream detail')
        operate_connection(self.business.id, self.connection.id, 'start')
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.state, 'error')
        self.assertNotIn('private', self.connection.last_error)

    def test_task_tenant_mismatch_fails_closed(self):
        other = Business.objects.create(name='Other')
        with self.assertRaises(Connection.DoesNotExist):
            operate_connection(other.id, self.connection.id, 'start')

    @patch('apps.whatsapp.providers.httpx.request')
    def test_text_response_contract_and_ambiguous_timeout(self, request):
        self.connection.token_encrypted = encrypt('secret')
        adapter = WPPConnectProvider(self.connection)
        request.return_value = Mock(is_success=True, json=lambda: {'status': 'success', 'response': [{'id': 'outbound-id'}]})
        self.assertEqual(adapter.send_text('919876543210@c.us', 'Hello'), 'outbound-id')
        self.assertEqual(request.call_args.kwargs['json']['phone'], ['919876543210@c.us'])
        request.side_effect = httpx.ReadTimeout('private')
        with self.assertRaises(AmbiguousSend):
            adapter.send_text('919876543210@c.us', 'Hello')
        request.side_effect = httpx.ConnectError('private')
        with self.assertRaises(SafeToRetry):
            adapter.send_text('919876543210@c.us', 'Hello')

    @patch.object(WPPConnectProvider, 'request')
    def test_lid_recipient_is_sent_in_provider_native_format(self, request):
        adapter = WPPConnectProvider(self.connection)
        request.return_value = {'status': 'success', 'response': [{'id': {'_serialized': 'outbound-lid'}}]}
        self.assertEqual(adapter.send_text('267000387@lid', 'Hello'), 'outbound-lid')
        self.assertEqual(request.call_args.args[2]['phone'], ['267000387@lid'])
        self.assertTrue(request.call_args.args[2]['isLid'])

    @patch.object(WPPConnectProvider, 'request')
    def test_lid_mapping_is_read_only_and_validated(self, request):
        adapter = WPPConnectProvider(self.connection)
        request.return_value = {
            'lid': {'_serialized': '267000387@lid'},
            'phoneNumber': {'_serialized': '12025550149@c.us'}, 'contact': {},
        }
        self.assertEqual(adapter.resolve_recipient('267000387@lid'), '12025550149@c.us')
        self.assertEqual(request.call_args.args, ('GET', 'contact/pn-lid/267000387%40lid'))
