from datetime import timedelta
from uuid import uuid4

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.tenants.models import Business
from apps.accounts.models import User
from apps.tenants.models import Membership
from apps.whatsapp.models import Connection
from config.asgi import application
from .models import GuestSession, Message, WebsiteWidget


@override_settings(
    WPPCONNECT_ENABLED=False,
    WPPCONNECT_URL='http://127.0.0.1:1',
    CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}},
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'guest-tests'}},
)
class GuestTests(TransactionTestCase):
    origin = 'https://support.example'

    def setUp(self):
        business = Business.objects.create(name='Support')
        endpoint = Connection.objects.create(business=business, provider='website', name='Chat', state='connected', desired_state='open')
        self.widget = WebsiteWidget.objects.create(business=business, connection=endpoint, origins=[self.origin], enabled=True)
        self.base = f'/api/guest/{self.widget.public_key}/'
        self.client = APIClient(HTTP_ORIGIN=self.origin, REMOTE_ADDR=f'guest-{uuid4()}')

    def test_anonymous_history_origin_expiration_and_deduplication(self):
        self.assertEqual(self.client.options(self.base)['Access-Control-Allow-Origin'], self.origin)
        self.assertEqual(self.client.get(self.base, HTTP_ORIGIN='https://foreign.example').status_code, 403)
        self.assertEqual(self.client.get(self.base + 'messages/', HTTP_AUTHORIZATION='Bearer ' + 'a' * 43).status_code, 403)
        token = self.client.post(self.base).data['token']
        auth = {'HTTP_AUTHORIZATION': f'Bearer {token}'}
        self.assertEqual(self.client.get(self.base + 'messages/').status_code, 403)
        payload = {'id': str(uuid4()), 'content': 'I need help'}
        self.assertEqual(self.client.post(self.base + 'messages/', payload, format='json', **auth).status_code, 201)
        self.assertEqual(self.client.post(self.base + 'messages/', payload, format='json', **auth).status_code, 200)
        self.assertEqual(Message.objects.count(), 1)
        messages = self.client.get(self.base + 'messages/', **auth).data['messages']
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]['content'], 'I need help')
        self.assertEqual(messages[0]['direction'], 'inbound')
        self.assertIsNotNone(messages[0]['created_at'])
        other = Business.objects.create(name='Other')
        endpoint = Connection.objects.create(business=other, provider='website', name='Chat')
        foreign = WebsiteWidget.objects.create(business=other, connection=endpoint, enabled=True, origins=[self.origin])
        self.assertEqual(self.client.get(f'/api/guest/{foreign.public_key}/messages/', **auth).status_code, 403)
        GuestSession.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.client.get(self.base + 'messages/', **auth).status_code, 403)

    def test_suggestion_can_be_sent_and_resumed(self):
        self.widget.suggestions = ['When is support available?']
        self.widget.save(update_fields=['suggestions'])
        question = self.client.get(self.base).data['suggestions'][0]
        self.assertEqual(question, 'When is support available?')
        token = self.client.post(self.base).data['token']
        resumed = APIClient(HTTP_ORIGIN=self.origin)
        headers = {'HTTP_AUTHORIZATION': f'Bearer {token}'}
        self.assertEqual(resumed.post(self.base + 'messages/', {'id': str(uuid4()), 'content': question}, format='json', **headers).status_code, 201)
        self.assertEqual(resumed.get(self.base + 'messages/', **headers).data['messages'][0]['content'], question)

    def test_session_rate_limit(self):
        for _ in range(15):
            self.assertEqual(self.client.post(self.base).status_code, 201)
        self.assertEqual(self.client.post(self.base).status_code, 429)

    def test_public_whatsapp_link_uses_only_configured_destination(self):
        url = self.base + 'whatsapp-link/'
        self.assertIsNone(self.client.get(url).data['url'])
        self.assertIn('business number', self.client.get(url).data['detail'])
        self.widget.enabled = False
        self.widget.whatsapp_number = '+12025550149'
        self.widget.whatsapp_message = 'Hi, support!'
        self.widget.save(update_fields=['enabled', 'whatsapp_number', 'whatsapp_message'])
        self.assertEqual(self.client.get(url).data['url'], 'https://wa.me/12025550149?text=Hi%2C+support%21')
        self.assertEqual(self.client.get(url, HTTP_ORIGIN='https://unauthorized.example').status_code, 403)
        self.assertFalse(GuestSession.objects.exists())

    def test_widget_saves_normalized_business_number(self):
        user = User.objects.create_user(username='link-admin')
        Membership.objects.create(business=self.widget.business, user=user, role='admin')
        admin = APIClient()
        admin.force_authenticate(user)
        url = f'/api/workspaces/{self.widget.business_id}/widget/'
        self.assertEqual(admin.patch(url, {'whatsapp_number': '+1 (202) 555-0149'}, format='json').data['whatsapp_number'], '12025550149')
        for number in ('+12aaa3456789', '0001234567', '+1 2'):
            self.assertEqual(admin.patch(url, {'whatsapp_number': number}, format='json').status_code, 400)

    def test_optional_mobile_is_encrypted_and_only_visible_to_workspace_staff(self):
        token = self.client.post(self.base).data['token']
        headers = {'HTTP_AUTHORIZATION': f'Bearer {token}'}
        contact_url = self.base + 'profile/'
        session = GuestSession.objects.get()
        item = session.conversation
        crm_url = f'/api/workspaces/{item.business_id}/conversations/{item.id}/'
        authorized = User.objects.create_user(username='authorized-phone-agent')
        foreign = User.objects.create_user(username='foreign-phone-agent')
        Membership.objects.create(user=authorized, business=item.business, role='agent')
        Membership.objects.create(user=foreign, business=Business.objects.create(name='Foreign'), role='admin')
        crm = APIClient()
        crm.force_authenticate(authorized)
        self.assertEqual(crm.get(crm_url).data['contact']['phone'], '')
        self.assertEqual(self.client.patch(contact_url, {'mobile': '555'}, format='json', **headers).status_code, 400)
        result = self.client.patch(contact_url, {'mobile': '+1 (202) 555-0149', 'email': 'Visitor@Example.com'}, format='json', **headers)
        self.assertTrue(result.data['mobile_shared'])
        self.assertTrue(result.data['email_shared'])
        contact = item.contact
        contact.refresh_from_db()
        self.assertTrue(contact.mobile_encrypted)
        self.assertTrue(contact.email_encrypted)
        self.assertNotIn('12025550149', contact.mobile_encrypted)
        self.assertNotIn('visitor@example.com', contact.email_encrypted)
        self.assertEqual(contact.phone, '')
        self.assertEqual(crm.get(crm_url).data['contact']['phone'], '+12025550149')
        self.assertEqual(crm.get(crm_url).data['contact']['email'], 'visitor@example.com')
        self.assertEqual(crm.patch(crm_url, {'internal_note': 'Follow up tomorrow.'}, format='json').status_code, 200)
        self.assertEqual(crm.get(crm_url).data['internal_note'], 'Follow up tomorrow.')
        self.assertNotIn('internal_note', crm.get(crm_url.rsplit(str(item.id), 1)[0]).data['results'][0])
        public = self.client.get(self.base + 'messages/', **headers).data
        self.assertNotIn('internal_note', public)
        self.assertNotIn('mobile', public)
        self.assertNotIn('12025550149', str(public))
        self.assertNotIn('visitor@example.com', str(public))
        outsider = APIClient()
        outsider.force_authenticate(foreign)
        self.assertEqual(outsider.get(crm_url).status_code, 404)
        self.assertEqual(self.client.patch(contact_url, {'mobile': '', 'email': ''}, format='json', **headers).status_code, 200)
        self.assertEqual(crm.get(crm_url).data['contact']['phone'], '')
        self.assertEqual(crm.get(crm_url).data['contact']['email'], '')

    def test_numbered_guests_are_visible_only_in_own_workspace_and_keep_separate_history(self):
        user = User.objects.create_user(username='guest-inbox-admin')
        business = self.widget.business
        Membership.objects.create(business=business, user=user, role='admin')
        other = Business.objects.create(name='Other workspace')
        Membership.objects.create(business=other, user=user, role='admin')
        crm = APIClient()
        crm.force_authenticate(user)

        for index in (1, 2):
            token = self.client.post(self.base).data['token']
            headers = {'HTTP_AUTHORIZATION': f'Bearer {token}'}
            self.assertEqual(self.client.post(self.base + 'messages/', {
                'id': str(uuid4()), 'content': f'Message {index}',
            }, format='json', **headers).status_code, 201)
            self.assertEqual(self.client.patch(self.base + 'profile/', {
                'display_name': f'Visitor {index}',
            }, format='json', **headers).status_code, 200)
            data = self.client.get(self.base + 'messages/', **headers).data
            self.assertEqual(data['display_name'], f'Visitor {index}')
            self.assertEqual([item['content'] for item in data['messages']], [f'Message {index}'])

        route = f'/api/workspaces/{business.id}/'
        inbox = crm.get(route + 'conversations/?channel=website').data
        self.assertEqual(inbox['count'], 2)
        self.assertEqual({item['contact']['guest_label'] for item in inbox['results']}, {'Guest #0001', 'Guest #0002'})
        self.assertTrue(all(item['unread_count'] == 1 for item in inbox['results']))
        self.assertEqual(crm.get(route + 'conversations/?search=Guest%20%230001').data['count'], 1)
        self.assertEqual(crm.get(route + 'dashboard/').data['website_conversations'], 2)
        self.assertEqual(crm.get(f'/api/workspaces/{other.id}/conversations/?channel=website').data['count'], 0)

    @override_settings(DEBUG=True)
    def test_publish_requires_valid_origin_and_uses_saved_allowlist(self):
        user = User.objects.create_user(username='widget-admin')
        Membership.objects.create(business=self.widget.business, user=user, role='admin')
        client = APIClient()
        client.force_authenticate(user)
        url = f'/api/workspaces/{self.widget.business_id}/widget/'
        self.assertEqual(client.patch(url, {'enabled': False, 'origins': []}, format='json').status_code, 200)
        response = client.patch(url, {'enabled': True}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('origins', response.data)
        self.assertEqual(client.patch(url, {'origins': ['https://example.test/path']}, format='json').status_code, 400)
        origins = ['http://127.0.0.1:5500', 'http://localhost:5500']
        self.assertEqual(client.patch(url, {'origins': origins}, format='json').status_code, 200)
        self.assertEqual(client.patch(url, {'enabled': True}, format='json').status_code, 200)
        for origin in origins:
            self.assertEqual(self.client.get(self.base, HTTP_ORIGIN=origin).status_code, 200)
        self.assertEqual(self.client.get(self.base, HTTP_ORIGIN='http://localhost:5501').status_code, 403)

    def test_handoff_takeover_reply_and_return_to_ai(self):
        from apps.ai_engine.models import AISettings, ReplyJob

        AISettings.objects.create(business=self.widget.business, enabled=True)
        headers = {'HTTP_AUTHORIZATION': f'Bearer {self.client.post(self.base).data["token"]}'}
        session = GuestSession.objects.get()
        with self.capture_reply_enqueue():
            self.client.post(self.base + 'messages/', {'id': str(uuid4()), 'content': 'Help'}, format='json', **headers)
        self.assertEqual(self.client.post(self.base + 'handoff/', **headers).status_code, 204)
        self.assertEqual(self.client.get(self.base + 'messages/', **headers).data['mode'], 'waiting')
        self.assertEqual(ReplyJob.objects.get().state, 'skipped')

        agent = User.objects.create_user(username='assigned')
        other = User.objects.create_user(username='other-agent')
        for user in (agent, other):
            Membership.objects.create(business=self.widget.business, user=user, role='agent')
        crm = APIClient()
        crm.force_authenticate(agent)
        url = f'/api/workspaces/{self.widget.business_id}/conversations/{session.conversation_id}/'
        self.assertEqual(crm.patch(url, {'reply_mode': 'manual'}, format='json').data['assigned_agent'], agent.id)
        payload = {'client_id': str(uuid4()), 'content': 'I can help.'}
        denied = APIClient()
        denied.force_authenticate(other)
        self.assertEqual(denied.post(url + 'messages/', payload, format='json').status_code, 403)
        self.assertEqual(crm.post(url + 'messages/', payload, format='json').status_code, 201)
        history = self.client.get(self.base + 'messages/', **headers).data
        self.assertEqual(history['mode'], 'manual')
        self.assertEqual(history['messages'][-1]['content'], payload['content'])
        self.assertEqual(crm.patch(url, {'reply_mode': 'ai'}, format='json').data['reply_mode'], 'ai')
        self.assertEqual(self.client.get(self.base + 'messages/', **headers).data['mode'], 'ai')

    def capture_reply_enqueue(self):
        from unittest.mock import patch

        return patch('apps.ai_engine.tasks.enqueue')

    def test_only_admin_can_export_or_delete_guest_chat(self):
        token = self.client.post(self.base).data['token']
        auth = {'HTTP_AUTHORIZATION': f'Bearer {token}'}
        content = {'id': str(uuid4()), 'content': 'Can you help?'}
        self.client.post(self.base + 'messages/', content, format='json', **auth)
        session = GuestSession.objects.get()
        business = self.widget.business
        base = f'/api/workspaces/{business.id}/conversations/{session.conversation_id}/guest-data/'
        user = User.objects.create_user(username='staff')
        membership = Membership.objects.create(business=business, user=user, role='agent')
        staff = APIClient()
        staff.force_authenticate(user)
        self.assertEqual(staff.get(base).status_code, 403)
        self.assertEqual(staff.delete(base).status_code, 403)
        membership.role = 'admin'
        membership.save(update_fields=['role'])
        self.assertEqual(staff.get(base).data['messages'][0]['content'], content['content'])
        self.assertEqual(staff.delete(base).status_code, 204)
        self.assertEqual(self.client.get(self.base + 'messages/', **auth).status_code, 403)
        self.assertFalse(Message.objects.exists())

    def test_realtime_only_notifies_authenticated_guest(self):
        token = self.client.post(self.base).data['token']
        conversation_id = GuestSession.objects.get().conversation_id

        async def scenario():
            ws = WebsocketCommunicator(application, f'/ws/guest/{self.widget.public_key}/', headers=[(b'origin', self.origin.encode())])
            self.assertTrue((await ws.connect())[0])
            await ws.send_to(text_data=token)
            self.assertEqual(await ws.receive_json_from(), {'kind': 'ready'})
            await get_channel_layer().group_send(f'guest.{uuid4()}', {'type': 'guest.event'})
            self.assertTrue(await ws.receive_nothing(timeout=.05))
            await get_channel_layer().group_send(f'guest.{conversation_id}', {'type': 'guest.event'})
            self.assertEqual(await ws.receive_json_from(), {'kind': 'updated'})
            await ws.disconnect()

            resumed = WebsocketCommunicator(application, f'/ws/guest/{self.widget.public_key}/', headers=[(b'origin', self.origin.encode())])
            self.assertTrue((await resumed.connect())[0])
            await resumed.send_to(text_data=token)
            self.assertEqual(await resumed.receive_json_from(), {'kind': 'ready'})
            await get_channel_layer().group_send(f'guest.{conversation_id}', {'type': 'guest.event'})
            self.assertEqual(await resumed.receive_json_from(), {'kind': 'updated'})
            self.assertTrue(await resumed.receive_nothing(timeout=.05))
            await resumed.disconnect()

        async_to_sync(scenario)()
