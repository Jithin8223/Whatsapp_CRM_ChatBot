from datetime import timedelta
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken
from apps.accounts.models import User, AuthSession
from apps.tenants.models import Business, Membership
from config.asgi import application


@override_settings(CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class WebsocketTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='socket-user')
        self.business = Business.objects.create(name='Mine')
        self.other = Business.objects.create(name='Other')
        Membership.objects.create(user=self.user, business=self.business, role='agent')
        self.session = AuthSession.objects.create(user=self.user, expires_at=timezone.now() + timedelta(hours=1))
        token = AccessToken.for_user(self.user)
        token['sid'] = str(self.session.id)
        self.cookie = f'crm_access={token}'.encode()

    def socket(self, business=None, cookie=True, origin=b'http://localhost:5173'):
        headers = [(b'origin', origin)]
        if cookie:
            headers.append((b'cookie', self.cookie))
        return WebsocketCommunicator(application, f'/ws/workspaces/{business or self.business.id}/', headers=headers)

    def test_unauthenticated_and_foreign_workspace_denied(self):
        async def scenario():
            for ws in (self.socket(cookie=False), self.socket(self.other.id), self.socket(origin=b'https://evil.example')):
                connected, _ = await ws.connect()
                self.assertFalse(connected)
                await ws.disconnect()
        async_to_sync(scenario)()

    def test_tenant_groups_and_logout_revocation(self):
        async def scenario():
            ws = self.socket()
            connected, _ = await ws.connect()
            self.assertTrue(connected)
            self.assertEqual((await ws.receive_json_from())['kind'], 'ready')
            layer = get_channel_layer()
            await layer.group_send(f'business.{self.other.id}', {'type': 'crm.event', 'kind': 'secret', 'object_id': 'private'})
            self.assertTrue(await ws.receive_nothing(timeout=.05))
            await layer.group_send(f'business.{self.business.id}', {'type': 'crm.event', 'kind': 'message.created', 'object_id': 'mine'})
            self.assertEqual((await ws.receive_json_from())['object_id'], 'mine')
            await database_sync_to_async(AuthSession.objects.filter(id=self.session.id).delete)()
            await ws.send_json_to({'kind': 'ping'})
            self.assertEqual((await ws.receive_output())['code'], 4401)
            await ws.disconnect()
        async_to_sync(scenario)()

    def test_client_cannot_select_groups(self):
        async def scenario():
            ws = self.socket()
            await ws.connect()
            await ws.receive_json_from()
            await ws.send_json_to({'kind': 'subscribe', 'group': f'business.{self.other.id}'})
            self.assertEqual((await ws.receive_output())['code'], 4400)
            await ws.disconnect()
        async_to_sync(scenario)()
