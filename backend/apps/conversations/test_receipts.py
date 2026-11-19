"""Delivery and read receipts: WhatsApp acks (WPPConnect) and website widget confirmations."""
import base64
import uuid

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.ai_engine.models import AISettings
from apps.tenants.models import Business, Membership
from apps.whatsapp.models import Connection
from .models import Contact, Conversation, DeliveryEvent, Message, WebhookEvent
from .services import process_event

SECRET = 'receipt-secret'


@override_settings(WPPCONNECT_ENABLED=True, WPPCONNECT_WEBHOOK_SECRET=SECRET, DEBUG=True,
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'receipts'}})
class WhatsAppReceiptTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name='Receipts')
        self.connection = Connection.objects.create(business=self.business, name='Line', state='connected', desired_state='open', token_encrypted='x')
        contact = Contact.objects.create(business=self.business, whatsapp_id='15550001111@c.us')
        self.thread = Conversation.objects.create(business=self.business, connection=self.connection, contact=contact)
        self.message = Message.objects.create(business=self.business, connection=self.connection, conversation=self.thread,
                                              direction='outbound', content='Hello', provider_id='true_out_1', send_state='submitted')
        self.auth = 'Basic ' + base64.b64encode(f'wppconnect:{SECRET}'.encode()).decode()

    def ack(self, value, provider_id='true_out_1', connection=None):
        connection = connection or self.connection
        response = APIClient().post('/internal/wppconnect/', {'session': connection.session, 'event': 'onack', 'id': provider_id, 'ack': value},
                                    format='json', HTTP_AUTHORIZATION=self.auth)
        for event in WebhookEvent.objects.filter(processed_at__isnull=True).order_by('created_at'):
            process_event(event.business_id, event.id)
        self.message.refresh_from_db()
        return response

    def test_progression_is_monotonic_and_idempotent(self):
        self.assertEqual(self.ack(1).status_code, 202)
        self.assertEqual((self.message.delivery_status, self.message.delivered_at, self.message.read_at), ('sent', None, None))
        self.ack(2)
        self.assertEqual(self.message.delivery_status, 'delivered')
        self.assertIsNotNone(self.message.delivered_at)
        self.assertIsNone(self.message.read_at)  # never "read" without a read receipt
        self.ack(2)  # duplicate
        self.ack(1)  # late, older event
        self.assertEqual(self.message.delivery_status, 'delivered')
        self.ack(3)
        read_at = self.message.read_at
        self.assertEqual(self.message.delivery_status, 'read')
        self.ack(1)
        self.ack(3)
        self.assertEqual((self.message.delivery_status, self.message.read_at), ('read', read_at))
        self.assertEqual(DeliveryEvent.objects.filter(provider_id='true_out_1').count(), 3)

    def test_out_of_order_read_before_delivered_and_played(self):
        self.ack(3)
        self.assertEqual(self.message.delivery_status, 'read')
        self.assertIsNotNone(self.message.delivered_at)
        self.ack(4)  # voice note played still counts as read
        self.assertEqual(self.message.delivery_status, 'read')

    def test_failure_acks_are_accepted_and_shown_only_without_confirmation(self):
        self.assertEqual(self.ack(-4).status_code, 202)  # previously rejected (only -1..4 accepted)
        self.assertEqual(self.message.delivery_status, 'failed')
        self.assertIn('too large', self.message.last_error)
        other = Message.objects.create(business=self.business, connection=self.connection, conversation=self.thread,
                                       direction='outbound', content='Hi', provider_id='true_out_2', send_state='submitted')
        self.ack(1, 'true_out_2')
        self.ack(-1, 'true_out_2')
        other.refresh_from_db()
        self.assertEqual(other.delivery_status, 'sent')  # a confirmed send is not undone by a later error ack
        self.assertEqual(self.ack(-9).status_code, 400)

    def test_peer_ack_is_not_a_customer_receipt(self):
        self.ack(5)
        self.assertEqual(self.message.delivery_status, 'pending')
        self.assertIsNone(self.message.delivered_at)

    def test_acks_are_scoped_to_their_connection_and_workspace(self):
        other = Business.objects.create(name='Other')
        foreign = Connection.objects.create(business=other, name='Line', state='connected', desired_state='open', token_encrypted='x')
        self.ack(3, connection=foreign)  # same provider id, different workspace connection
        self.assertEqual(self.message.delivery_status, 'pending')
        self.assertFalse(DeliveryEvent.objects.filter(connection=self.connection).exists())


@override_settings(DEBUG=True, CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'web-receipts'}})
class WebsiteReceiptTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name='Web')
        self.admin = User.objects.create_user(username='web-admin')
        Membership.objects.create(business=self.business, user=self.admin, role='admin')
        AISettings.objects.create(business=self.business, enabled=True)
        self.staff = APIClient()
        self.staff.force_authenticate(self.admin)
        self.root = f'/api/workspaces/{self.business.id}/'
        key = self.staff.patch(self.root + 'widget/', {'title': 'Help', 'welcome': 'Hi', 'color': '#123b30',
                                                         'origins': ['https://shop.example'], 'enabled': True}, format='json').data['public_key']
        self.base = f'/api/guest/{key}/'
        self.guest = APIClient(HTTP_ORIGIN='https://shop.example', REMOTE_ADDR=f'g-{uuid.uuid4()}')
        token = self.guest.post(self.base).data['token']
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {token}'}
        self.guest.post(self.base + 'messages/', {'id': str(uuid.uuid4()), 'content': 'Hello'}, format='json', **self.auth)
        self.thread = Conversation.objects.get(connection__provider='website')
        self.staff.post(self.root + f'conversations/{self.thread.id}/takeover/')
        self.reply = self.staff.post(self.root + f'conversations/{self.thread.id}/messages/',
                                     {'client_id': str(uuid.uuid4()), 'content': 'Hi, Sam here'}, format='json').data

    def status(self):
        return Message.objects.get(pk=self.reply['id'])

    def test_sent_then_delivered_then_read_only_when_confirmed(self):
        self.assertEqual(self.reply['delivery_status'], 'sent')
        history = self.guest.get(self.base + 'messages/', **self.auth).data['messages']
        self.assertEqual(history[-1]['id'], self.reply['id'])
        self.assertIsNone(history[0]['id'])  # visitor's own messages carry no receipt id
        self.assertEqual(self.status().delivery_status, 'sent')  # fetching alone confirms nothing
        self.guest.post(self.base + 'receipts/', {'delivered': [self.reply['id']]}, format='json', **self.auth)
        self.assertEqual(self.status().delivery_status, 'delivered')
        self.assertIsNone(self.status().read_at)
        self.guest.post(self.base + 'receipts/', {'read': [self.reply['id']]}, format='json', **self.auth)
        message = self.status()
        self.assertEqual(message.delivery_status, 'read')
        self.assertIsNotNone(message.read_at)
        self.guest.post(self.base + 'receipts/', {'delivered': [self.reply['id']]}, format='json', **self.auth)
        self.assertEqual(self.status().delivery_status, 'read')  # no downgrade
        detail = self.staff.get(self.root + f'conversations/{self.thread.id}/messages/').data['results'][0]
        self.assertEqual((detail['delivery_status'], bool(detail['read_at'])), ('read', True))

    def test_receipts_only_touch_this_visitors_outbound_messages(self):
        inbound = Message.objects.get(conversation=self.thread, direction='inbound')
        other_token = self.guest.post(self.base).data['token']
        response = self.guest.post(self.base + 'receipts/', {'read': [self.reply['id'], str(inbound.id)]}, format='json',
                                   HTTP_AUTHORIZATION=f'Bearer {other_token}')
        self.assertEqual(response.data['updated'], 0)
        self.assertEqual(self.status().delivery_status, 'sent')
        self.assertEqual(self.guest.post(self.base + 'receipts/', {'read': [self.reply['id']]}, format='json').status_code, 403)

    def test_team_viewing_marks_incoming_seen_without_customer_receipt(self):
        inbound = Message.objects.get(conversation=self.thread, direction='inbound')
        self.assertIsNone(inbound.seen_at)
        self.staff.post(self.root + f'conversations/{self.thread.id}/read/')
        inbound.refresh_from_db()
        self.assertIsNotNone(inbound.seen_at)
        self.assertEqual(self.status().delivery_status, 'sent')  # staff viewing is not a customer read
