import base64
import uuid
from datetime import timedelta
from unittest.mock import patch
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.tenants.models import Business, Membership
from apps.whatsapp.models import Connection
from apps.whatsapp.providers import AmbiguousSend, SafeToRetry
from apps.ai_engine.models import AISettings, KnowledgeEntry, ReplyJob
from apps.ai_engine.tasks import run_replies
from .models import Contact, Conversation, Message, DeliveryEvent, WebhookEvent
from .services import process_event, queue_message, dispatch, record_inbound
from .tasks import recover_work, process_webhook


@override_settings(WPPCONNECT_WEBHOOK_SECRET='test-callback-secret', WPPCONNECT_ENABLED=True)
class MessagingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner')
        self.business = Business.objects.create(name='Mine')
        self.other = Business.objects.create(name='Other')
        Membership.objects.create(business=self.business, user=self.user, role='admin')
        self.connection = Connection.objects.create(
            business=self.business, name='Sales', state='connected', desired_state='open', token_encrypted='mock-provider-token',
        )
        self.contact = Contact.objects.create(business=self.business, whatsapp_id='919876543210@c.us')
        self.conversation = Conversation.objects.create(business=self.business, connection=self.connection, contact=self.contact)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.base = f'/api/workspaces/{self.business.id}/'
        self.auth = 'Basic ' + base64.b64encode(b'wppconnect:test-callback-secret').decode()

    def webhook(self, payload=None, auth=None):
        payload = payload or {'session': self.connection.session, 'event': 'onmessage', 'id': 'incoming-1', 'from': self.contact.whatsapp_id, 'body': 'Hello', 'type': 'chat', 'timestamp': 1794910000}
        client = APIClient()
        return client.post('/internal/wppconnect/', payload, format='json', HTTP_AUTHORIZATION=auth or self.auth)

    def test_webhook_auth_and_unknown_session(self):
        self.assertEqual(self.webhook(auth='Basic invalid').status_code, 401)
        self.assertEqual(self.webhook({'session': 'unknown', 'event': 'onmessage'}).status_code, 404)
        self.assertEqual(WebhookEvent.objects.count(), 0)

    @patch('apps.conversations.channels.provider_for')
    def test_simulated_whatsapp_webhook_automation_takeover_and_delivery(self, factory):
        AISettings.objects.create(business=self.business, enabled=True)
        KnowledgeEntry.objects.create(business=self.business, question='Business hours', answer='Support is available on weekdays.')
        opt_in = self.client.patch(
            self.base + f'connections/{self.connection.id}/automation/', {'enabled': True}, format='json',
        )
        self.assertEqual(opt_in.status_code, 200)
        self.assertTrue(opt_in.data['operational'])
        self.assertNotIn('token_encrypted', opt_in.data)
        payload = {'session': self.connection.session, 'event': 'onmessage', 'id': 'sim-in',
                   'from': '15550123456@c.us', 'type': 'chat', 'body': 'What are your working hours?'}
        self.assertEqual(self.webhook(payload).status_code, 202)
        self.assertTrue(self.webhook(payload).data['duplicate'])
        event = WebhookEvent.objects.get()
        process_event(self.business.id, event.id)
        thread = Conversation.objects.get(contact__whatsapp_id=payload['from'])
        self.assertEqual(thread.business_id, self.business.id)
        self.assertEqual(thread.reply_mode, 'ai')
        self.assertEqual(self.client.get(self.base + 'conversations/?channel=whatsapp').data['count'], 2)
        run_replies(self.business.id, thread.id)
        run_replies(self.business.id, thread.id)
        answer = thread.messages.get(direction='outbound')
        self.assertTrue(answer.automated)
        self.assertEqual(answer.send_state, 'queued')
        factory.return_value.send_text.return_value = 'sim-auto'
        dispatch(self.business.id, thread.id)
        factory.return_value.send_text.assert_called_once_with(payload['from'], 'Support is available on weekdays.')
        self.webhook({'event': 'onack', 'session': self.connection.session, 'id': 'sim-auto', 'ack': 2})
        process_event(self.business.id, WebhookEvent.objects.get(processed_at__isnull=True).id)
        answer.refresh_from_db()
        self.assertEqual(answer.delivery_status, 'delivered')

        payload['id'] = 'sim-later'
        self.webhook(payload)
        process_event(self.business.id, WebhookEvent.objects.get(processed_at__isnull=True).id)
        run_replies(self.business.id, thread.id)
        queued = thread.messages.get(send_state='queued')
        url = self.base + f'conversations/{thread.id}/'
        self.assertEqual(self.client.patch(url, {'reply_mode': 'manual'}, format='json').data['assigned_agent'], self.user.id)
        queued.refresh_from_db()
        self.assertEqual(queued.send_state, 'cancelled')
        factory.return_value.send_text.return_value = 'sim-manual'
        response = self.client.post(url + 'messages/', {'client_id': str(uuid.uuid4()), 'content': 'An agent can help.'}, format='json')
        self.assertEqual(response.status_code, 201)
        dispatch(self.business.id, thread.id)
        factory.return_value.send_text.assert_called_with(payload['from'], 'An agent can help.')
        self.assertEqual(factory.return_value.send_text.call_count, 2)
        self.assertEqual(self.client.patch(url, {'reply_mode': 'ai'}, format='json').status_code, 200)
        payload['id'] = 'sim-after-return'
        self.webhook(payload)
        process_event(self.business.id, WebhookEvent.objects.get(processed_at__isnull=True).id)
        self.assertTrue(ReplyJob.objects.filter(message__provider_id='sim-after-return', state='queued').exists())

    def test_disconnected_whatsapp_refuses_opt_in_and_manual_send(self):
        self.connection.state = 'disconnected'
        self.connection.save(update_fields=['state'])
        url = self.base + f'connections/{self.connection.id}/'
        self.assertFalse(self.client.get(url).data['operational'])
        self.assertEqual(self.client.patch(url + 'automation/', {'enabled': True}, format='json').status_code, 400)
        result = self.client.post(self.base + f'conversations/{self.conversation.id}/messages/', {
            'client_id': str(uuid.uuid4()), 'content': 'Manual test',
        }, format='json')
        self.assertEqual(result.status_code, 400)
        self.assertFalse(self.conversation.messages.exists())

    def test_simulated_whatsapp_menu_and_provider_phone_metadata(self):
        self.connection.ai_enabled = True
        self.connection.save(update_fields=['ai_enabled'])
        AISettings.objects.create(business=self.business, enabled=True)
        KnowledgeEntry.objects.create(business=self.business, question='What services do you offer?', answer='We provide approved support.')
        payload = {'session': self.connection.session, 'event': 'onmessage', 'type': 'chat',
                   'from': '15550000@lid', 'sender': {'id': {'_serialized': '12025550149@c.us'}, 'pushname': 'Example'}}
        for index, body in enumerate(('Hi', '1', 'Hello', 'I need a human'), 1):
            payload.update(id=f'menu-{index}', body=body)
            self.assertEqual(self.webhook(payload).status_code, 202)
            process_event(self.business.id, WebhookEvent.objects.get(processed_at__isnull=True).id)
            thread = Conversation.objects.get(contact__whatsapp_id=payload['from'])
            run_replies(self.business.id, thread.id)
        self.assertEqual(thread.contact.crm_phone, '+12025550149')
        self.assertTrue(thread.contact.mobile_encrypted)
        self.assertEqual(thread.contact.phone, '')
        replies = list(thread.messages.filter(direction='outbound').order_by('created_at').values_list('content', flat=True))
        # The shared chatbot menu (not the retired legacy WhatsApp menu) drives WhatsApp.
        self.assertIn('1. View Products', replies[0])
        self.assertIn('product catalog is currently empty', replies[1])
        self.assertIn('1. View Products', replies[2])
        self.assertIn('human support', replies[3])
        thread.refresh_from_db()
        self.assertEqual(thread.reply_mode, 'waiting')
        payload.update(id='no-number', **{'from': '15550001@lid', 'sender': {}, 'body': 'Hi'})
        self.webhook(payload)
        process_event(self.business.id, WebhookEvent.objects.get(processed_at__isnull=True).id)
        self.assertEqual(Contact.objects.get(whatsapp_id='15550001@lid').crm_phone, '')

    def test_durable_dedup_and_incoming_persistence(self):
        self.assertEqual(self.webhook().status_code, 202)
        self.assertTrue(self.webhook().data['duplicate'])
        self.assertEqual(WebhookEvent.objects.count(), 1)
        event = WebhookEvent.objects.get()
        self.assertNotIn('Hello', event.payload_encrypted)
        process_event(self.business.id, event.id)
        process_event(self.business.id, event.id)
        self.assertEqual(Message.objects.count(), 1)
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.unread_count, 1)
        self.assertEqual(self.conversation.last_message.content, 'Hello')

    def test_structured_sender_identifier_is_accepted(self):
        payload = {
            'session': self.connection.session, 'event': 'onmessage',
            'id': {'_serialized': 'structured-incoming'},
            'from': {'_serialized': '15550123456@c.us'},
            'body': 'Hello', 'type': 'chat',
        }
        response = self.webhook(payload)
        self.assertEqual(response.status_code, 202)
        process_event(self.business.id, WebhookEvent.objects.get().id)
        self.assertTrue(Message.objects.filter(provider_id='structured-incoming', conversation__contact__whatsapp_id='15550123456@c.us').exists())

    @patch('apps.conversations.channels.provider_for')
    def test_website_messages_share_the_inbox_and_never_call_whatsapp(self, provider):
        endpoint = Connection.objects.create(
            business=self.business, name='Website chat', provider=Connection.WEBSITE_PROVIDER,
            state='connected', desired_state='open',
        )
        guest = Contact.objects.create(business=self.business, website_visitor_id=uuid.uuid4())
        foreign_guest = Contact.objects.create(business=self.other, website_visitor_id=uuid.uuid4())
        with self.assertRaises(Contact.DoesNotExist):
            record_inbound(self.business.id, endpoint.id, foreign_guest.id, 'foreign', 'Hello')
        inbound, created = record_inbound(self.business.id, endpoint.id, guest.id, 'guest-1', 'Hello')
        self.assertTrue(created)
        duplicate, created = record_inbound(self.business.id, endpoint.id, guest.id, 'guest-1', 'Hello')
        self.assertFalse(created)
        self.assertEqual(duplicate.id, inbound.id)
        self.assertEqual(inbound.conversation.unread_count, 1)
        inbox = self.client.get(self.base + 'conversations/?channel=website')
        self.assertEqual(inbox.data['count'], 1)
        self.assertEqual(inbox.data['results'][0]['id'], str(inbound.conversation_id))
        self.assertEqual(inbox.data['results'][0]['channel'], 'website')
        self.assertEqual(self.client.get(self.base + 'connections/').data[0]['id'], str(self.connection.id))
        self.assertEqual(self.webhook({'session': endpoint.session, 'event': 'onmessage'}).status_code, 404)
        reply_id = uuid.uuid4()
        messages = self.base + f'conversations/{inbound.conversation_id}/messages/'
        reply = self.client.post(messages, {'content': 'Welcome', 'client_id': str(reply_id)}, format='json')
        self.assertEqual(reply.status_code, 201)
        self.assertEqual(reply.data['send_state'], 'submitted')
        self.assertEqual(reply.data['delivery_status'], 'sent')
        duplicate_reply = self.client.post(messages, {'content': 'Welcome', 'client_id': str(reply_id)}, format='json')
        self.assertEqual(duplicate_reply.status_code, 200)
        self.assertEqual(duplicate_reply.data['id'], reply.data['id'])
        dispatch(self.business.id, inbound.conversation_id)
        provider.assert_not_called()

    def test_self_messages_and_groups_are_ignored(self):
        response = self.webhook({'session': self.connection.session, 'event': 'onmessage', 'fromMe': True})
        self.assertFalse(response.data['accepted'])
        self.assertEqual(WebhookEvent.objects.count(), 0)

    def test_inbound_wid_and_message_key_remote_preserve_conversation(self):
        payload = {
            'session': self.connection.session, 'event': 'unreadmessages',
            'id': {'_serialized': 'false_919876543210@c.us_key', 'remote': {
                'user': '919876543210', 'server': 's.whatsapp.net',
            }},
            'body': 'Hello',
        }
        self.assertEqual(self.webhook(payload).status_code, 202)
        process_event(self.business.id, WebhookEvent.objects.get().id)
        self.assertEqual(self.conversation.messages.get().content, 'Hello')
        self.assertEqual(Contact.objects.count(), 1)

    def test_non_direct_remote_cannot_fall_back_to_direct_sender(self):
        payload = {
            'session': self.connection.session, 'event': 'onmessage',
            'id': {'_serialized': 'false_group_key', 'remote': '12345@g.us'},
            'sender': {'id': self.contact.whatsapp_id},
        }
        self.assertEqual(self.webhook(payload).data['reason'], 'ignored_message')
        self.assertFalse(WebhookEvent.objects.exists())

    def test_task_mismatched_tenant_cannot_process(self):
        self.webhook()
        event = WebhookEvent.objects.get()
        process_event(self.other.id, event.id)
        self.assertFalse(Message.objects.exists())

    def test_conversation_isolation_and_foreign_relationships(self):
        foreign = Contact.objects.create(business=self.other, whatsapp_id='919999999999@c.us')
        self.assertEqual(self.client.get(f'/api/workspaces/{self.other.id}/conversations/').status_code, 404)
        self.assertEqual(self.client.post(self.base + 'conversations/', {'connection': str(self.connection.id), 'contact': str(foreign.id)}, format='json').status_code, 404)
        self.assertEqual(self.client.get(self.base + 'conversations/').data['count'], 1)

    @patch('apps.conversations.channels.provider_for')
    def test_send_idempotency_and_dispatch(self, factory):
        factory.return_value.send_text.return_value = 'provider-out-1'
        client_id = uuid.uuid4()
        first, created = queue_message(self.business.id, self.conversation.id, client_id, 'Hi')
        self.assertTrue(created)
        second, created = queue_message(self.business.id, self.conversation.id, client_id, 'Hi')
        self.assertFalse(created)
        self.assertEqual(first.id, second.id)
        dispatch(self.business.id, self.conversation.id)
        dispatch(self.business.id, self.conversation.id)
        factory.return_value.send_text.assert_called_once()
        first.refresh_from_db()
        self.assertEqual(first.send_state, 'submitted')
        self.assertEqual(first.provider_id, 'provider-out-1')

    def test_duplicate_manual_send_does_not_take_over_after_return_to_ai(self):
        self.connection.ai_enabled = True
        self.connection.save(update_fields=['ai_enabled'])
        AISettings.objects.create(business=self.business, enabled=True)
        client_id = uuid.uuid4()
        url = self.base + f'conversations/{self.conversation.id}/messages/'
        first = self.client.post(url, {'client_id': str(client_id), 'content': 'Hello'}, format='json')
        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.data['sender_type'], 'admin')
        detail = self.base + f'conversations/{self.conversation.id}/'
        self.assertEqual(self.client.patch(detail, {'reply_mode': 'ai'}, format='json').status_code, 200)

        duplicate = self.client.post(url, {'client_id': str(client_id), 'content': 'Hello'}, format='json')
        self.assertEqual(duplicate.status_code, 200)
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.reply_mode, 'ai')
        # Resuming AI keeps the owner: ownership and automation are independent.
        self.assertEqual(self.conversation.assigned_agent_id, self.user.id)

    @patch('apps.conversations.channels.provider_for')
    def test_ack_recorded_before_provider_id_is_reconciled_after_send(self, factory):
        factory.return_value.send_text.return_value = 'early-ack'
        item, _ = queue_message(self.business.id, self.conversation.id, uuid.uuid4(), 'Hi')
        DeliveryEvent.objects.create(
            business=self.business, connection=self.connection,
            provider_id='early-ack', ack=2,
        )
        dispatch(self.business.id, self.conversation.id)
        item.refresh_from_db()
        self.assertEqual(item.provider_id, 'early-ack')
        self.assertEqual(item.delivery_status, 'delivered')

    @patch('apps.conversations.channels.provider_for')
    def test_lid_send_persists_resolved_customer_number(self, factory):
        lid = Contact.objects.create(business=self.business, whatsapp_id='267000387@lid')
        thread = Conversation.objects.create(business=self.business, connection=self.connection, contact=lid)
        factory.return_value.resolve_recipient.return_value = '12025550149@c.us'
        factory.return_value.send_text.return_value = 'lid-outbound'
        queue_message(self.business.id, thread.id, uuid.uuid4(), 'Hello')
        dispatch(self.business.id, thread.id)
        lid.refresh_from_db()
        self.assertEqual(lid.crm_phone, '+12025550149')
        factory.return_value.send_text.assert_called_once_with('267000387@lid', 'Hello')

    @patch('apps.conversations.channels.provider_for')
    def test_lid_lookup_failure_does_not_prevent_native_lid_send(self, factory):
        lid = Contact.objects.create(business=self.business, whatsapp_id='267000387@lid')
        thread = Conversation.objects.create(business=self.business, connection=self.connection, contact=lid)
        factory.return_value.resolve_recipient.side_effect = SafeToRetry('Lookup unavailable')
        factory.return_value.send_text.return_value = 'native-lid-outbound'
        item, _ = queue_message(self.business.id, thread.id, uuid.uuid4(), 'Hello')
        dispatch(self.business.id, thread.id)
        item.refresh_from_db()
        self.assertEqual(item.send_state, 'submitted')
        self.assertEqual(item.provider_id, 'native-lid-outbound')
        factory.return_value.send_text.assert_called_once_with('267000387@lid', 'Hello')

    @patch('apps.conversations.channels.provider_for')
    def test_ambiguous_send_is_never_automatically_repeated(self, factory):
        factory.return_value.send_text.side_effect = AmbiguousSend('timeout')
        first, _ = queue_message(self.business.id, self.conversation.id, uuid.uuid4(), 'First')
        dispatch(self.business.id, self.conversation.id)
        dispatch(self.business.id, self.conversation.id)
        first.refresh_from_db()
        self.assertEqual(first.send_state, 'unknown')
        self.assertEqual(factory.return_value.send_text.call_count, 1)

    @patch('apps.conversations.channels.provider_for')
    def test_unknown_send_is_not_retried_and_does_not_block_later_message(self, factory):
        factory.return_value.send_text.side_effect = [AmbiguousSend('timeout'), 'provider-second']
        first, _ = queue_message(self.business.id, self.conversation.id, uuid.uuid4(), 'First')
        second, _ = queue_message(self.business.id, self.conversation.id, uuid.uuid4(), 'Second')
        dispatch(self.business.id, self.conversation.id)
        dispatch(self.business.id, self.conversation.id)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.send_state, 'unknown')
        self.assertEqual(second.send_state, 'submitted')
        self.assertEqual(second.provider_id, 'provider-second')
        self.assertEqual(factory.return_value.send_text.call_count, 2)

    @patch('apps.conversations.channels.provider_for')
    def test_safe_retry_is_bounded(self, factory):
        factory.return_value.send_text.side_effect = SafeToRetry('unreachable')
        item, _ = queue_message(self.business.id, self.conversation.id, uuid.uuid4(), 'Hi')
        for _ in range(3):
            Message.objects.filter(id=item.id).update(next_attempt_at=timezone.now() - timedelta(seconds=1))
            dispatch(self.business.id, self.conversation.id)
        item.refresh_from_db()
        self.assertEqual(item.send_state, 'failed')
        self.assertEqual(item.attempts, 3)

    def test_delivery_events_monotonic_and_separate(self):
        item = Message.objects.create(business=self.business, connection=self.connection, conversation=self.conversation, direction='outbound', content='Hi', provider_id='out1', send_state='submitted')
        for ack in (3, 1, 3):
            self.webhook({'session': self.connection.session, 'event': 'onack', 'id': {'_serialized': 'out1'}, 'ack': ack})
        for event in WebhookEvent.objects.all():
            process_event(self.business.id, event.id)
        item.refresh_from_db()
        self.assertEqual(item.delivery_status, 'read')
        self.assertEqual(DeliveryEvent.objects.count(), 2)

    def test_foreign_agent_assignment_denied(self):
        outsider = User.objects.create_user(username='other')
        Membership.objects.create(user=outsider, business=self.other, role='agent')
        result = self.client.patch(f'{self.base}conversations/{self.conversation.id}/', {'assigned_agent': outsider.id}, format='json')
        self.assertEqual(result.status_code, 404)

    def test_cross_tenant_database_relationship_rejected(self):
        foreign = Contact.objects.create(business=self.other, whatsapp_id='918888888888@c.us')
        with self.assertRaises(IntegrityError), transaction.atomic():
            Conversation.objects.create(business=self.business, connection=self.connection, contact=foreign)

    def test_webhook_payload_limit(self):
        client = APIClient()
        response = client.post('/internal/wppconnect/', {'x': 'a' * 270000}, format='json', HTTP_AUTHORIZATION=self.auth)
        self.assertEqual(response.status_code, 413)
