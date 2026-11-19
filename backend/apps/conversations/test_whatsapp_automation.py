"""Real-channel WhatsApp automation: webhook -> conversation -> reply job -> menu -> outbound.

WPPConnect is mocked at ``provider_for``; these tests prove the CRM pipeline, not
real WhatsApp delivery.
"""
import base64
import uuid
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.ai_engine.models import AISettings, ReplyJob
from apps.ai_engine.tasks import run_replies
from apps.ai_engine.workflows import HANDOFF
from apps.products.models import Product
from apps.tenants.models import Business, Membership
from apps.whatsapp.models import Connection
from apps.whatsapp.providers import AmbiguousSend, SafeToRetry
from .models import Conversation, Message, WebhookEvent
from .services import dispatch, enqueue
from .tasks import process_webhook

SECRET = 'automation-test-secret'
CUSTOMER = '15550123456@c.us'


@override_settings(WPPCONNECT_WEBHOOK_SECRET=SECRET, WPPCONNECT_ENABLED=True, DEBUG=True)
@patch('apps.conversations.channels.provider_for')
class RealWhatsAppAutomationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='automation-admin')
        self.business = Business.objects.create(name='Acme')
        Membership.objects.create(business=self.business, user=self.admin, role='admin')
        self.connection = Connection.objects.create(
            business=self.business, name='Support line', state='connected', desired_state='open',
            token_encrypted='mock-provider-token', ai_enabled=True,
        )
        AISettings.objects.create(business=self.business, enabled=True)
        self.alpha = Product.objects.create(
            business=self.business, name='Alpha Plan', price='100.00', currency='INR',
            description='Starter website package.', features=['Hosting', 'SSL'],
        )
        self.beta = Product.objects.create(
            business=self.business, name='Beta Plan', price='250.00', currency='INR',
            description='Online store package.', features=['Payments', 'Catalog'],
        )
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.base = f'/api/workspaces/{self.business.id}/'
        self.auth = 'Basic ' + base64.b64encode(f'wppconnect:{SECRET}'.encode()).decode()
        self.sequence = 0

    # -- helpers ---------------------------------------------------------------------
    def post_webhook(self, payload):
        return APIClient().post('/internal/wppconnect/', payload, format='json', HTTP_AUTHORIZATION=self.auth)

    def payload(self, body, message_id=None, sender=CUSTOMER, connection=None, **extra):
        self.sequence += 1
        data = {'session': (connection or self.connection).session, 'event': 'onmessage', 'type': 'chat',
                'from': sender, 'body': body, 't': 1794910000 + self.sequence}
        if message_id is not False:
            data['id'] = message_id or f'false_{sender}_{uuid.uuid4().hex}'
        data.update(extra)
        return data

    def deliver(self, body, provider, connection=None, sender=CUSTOMER, **extra):
        """Customer sends a WhatsApp message; run the worker steps synchronously."""
        connection = connection or self.connection
        response = self.post_webhook(self.payload(body, sender=sender, connection=connection, **extra))
        self.assertEqual(response.status_code, 202, response.data)
        for event in WebhookEvent.objects.filter(connection=connection, processed_at__isnull=True):
            process_webhook(str(connection.business_id), str(event.id))
        thread = Conversation.objects.get(connection=connection, contact__whatsapp_id=sender)
        run_replies(str(connection.business_id), str(thread.id))
        dispatch(str(connection.business_id), str(thread.id))
        return thread

    def last_reply(self, thread):
        return thread.messages.filter(direction='outbound').order_by('-created_at', '-id').first()

    def sent_texts(self, provider):
        return [call.args[1] for call in provider.return_value.send_text.call_args_list]

    def mock_send(self, provider):
        provider.return_value.send_text.side_effect = lambda recipient, text: f'out-{uuid.uuid4().hex}'

    # -- 1, 3, 4, 5, 6: enabled connection replies automatically end to end ----------
    def test_connected_ai_enabled_menu_products_and_details_without_approval(self, provider):
        self.mock_send(provider)
        thread = self.deliver('Hi', provider)
        self.assertEqual(thread.reply_mode, 'ai')
        self.assertIsNone(thread.assigned_agent_id)
        menu = self.last_reply(thread)
        self.assertTrue(menu.automated)
        self.assertEqual(menu.send_state, 'submitted')
        self.assertIn('Welcome to our business!', menu.content)
        for line in ('1. View Products', '2. Product Prices', '6. Other Questions', 'Reply with a number'):
            self.assertIn(line, menu.content)
        provider.return_value.send_text.assert_called_with(CUSTOMER, menu.content)

        listing = self.last_reply(self.deliver('1', provider)).content
        self.assertIn('1. Alpha Plan — Price: 100.00 INR', listing)
        self.assertIn('2. Beta Plan — Price: 250.00 INR', listing)
        details = self.last_reply(self.deliver('2', provider)).content
        for fact in ('Beta Plan', 'Price: 250.00 INR', 'Online store package.', 'Payments; Catalog', '1. Request a quotation'):
            self.assertIn(fact, details)
        self.assertNotIn('Alpha Plan', details)
        thread.refresh_from_db()
        self.assertEqual(thread.chatbot_state['state'], 'PRODUCT_DETAILS')
        self.assertEqual(len(self.sent_texts(provider)), 3)
        self.assertFalse(ReplyJob.objects.filter(state__in=('draft', 'handoff', 'failed')).exists())

    def test_greeting_variants_and_navigation_commands(self, provider):
        self.mock_send(provider)
        for greeting in ('Hello!', 'hiii', 'Hey 👋', 'START', 'Menu'):
            self.assertIn('1. View Products', self.last_reply(self.deliver(greeting, provider)).content)
        self.deliver('1', provider)
        self.deliver('1', provider)
        listing = self.last_reply(self.deliver('back', provider)).content
        self.assertIn('1. Alpha Plan', listing)
        self.assertIn('not in this list', self.last_reply(self.deliver('7', provider)).content)
        thread = self.deliver('restart', provider)
        self.assertIn('1. View Products', self.last_reply(thread).content)
        self.assertIn('not in the menu', self.last_reply(self.deliver('8', provider)).content)
        thread.refresh_from_db()
        self.assertEqual(thread.chatbot_state, {'state': 'MAIN_MENU'})

    # -- 2: AI disabled on the connection, then explicitly enabled --------------------
    def test_connected_ai_disabled_records_message_without_reply_then_enable(self, provider):
        self.mock_send(provider)
        self.connection.ai_enabled = False
        self.connection.save(update_fields=['ai_enabled'])
        listed = self.client.get(self.base + 'connections/').data[0]
        self.assertTrue(listed['operational'])
        self.assertFalse(listed['ai_enabled'])
        self.assertTrue(listed['workspace_ai_enabled'])
        self.assertFalse(listed['auto_reply_active'])

        thread = self.deliver('Hi', provider)
        self.assertEqual(thread.reply_mode, 'manual')
        self.assertTrue(thread.messages.filter(direction='inbound', content='Hi').exists())
        self.assertFalse(ReplyJob.objects.exists())
        provider.return_value.send_text.assert_not_called()

        response = self.client.patch(self.base + f'connections/{self.connection.id}/automation/', {'enabled': True}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['auto_reply_active'])
        self.connection.refresh_from_db()
        self.assertTrue(self.connection.ai_enabled)
        # The unowned conversation created before automation now switches to AI.
        thread = self.deliver('Hi', provider)
        self.assertEqual(thread.reply_mode, 'ai')
        self.assertIn('1. View Products', self.last_reply(thread).content)

    def test_workspace_switch_off_blocks_replies_and_reports_status(self, provider):
        AISettings.objects.filter(business=self.business).update(enabled=False)
        self.assertFalse(self.client.get(self.base + 'connections/').data[0]['auto_reply_active'])
        thread = self.deliver('Hi', provider)
        self.assertFalse(ReplyJob.objects.exists())
        detail = self.client.get(self.base + f'conversations/{thread.id}/').data
        self.assertFalse(detail['automation_available'])
        self.assertIn('disabled in AI settings', detail['automation_blocker'])

    # -- 7, 8: human takeover and explicit reactivation ------------------------------
    def test_human_takeover_blocks_replies_until_explicitly_reactivated(self, provider):
        self.mock_send(provider)
        thread = self.deliver('Hi', provider)
        url = self.base + f'conversations/{thread.id}/'
        taken = self.client.patch(url, {'reply_mode': 'manual'}, format='json')
        self.assertEqual(taken.data['assigned_agent'], self.admin.id)
        for body in ('1', 'menu', 'Hello'):
            self.deliver(body, provider)
        thread.refresh_from_db()
        self.assertEqual(thread.reply_mode, 'manual')
        self.assertEqual(ReplyJob.objects.filter(message__conversation=thread).count(), 1)
        self.assertEqual(len(self.sent_texts(provider)), 1)

        self.assertEqual(self.client.patch(url, {'reply_mode': 'ai'}, format='json').status_code, 200)
        self.assertIn('1. Alpha Plan', self.last_reply(self.deliver('1', provider)).content)
        thread.refresh_from_db()
        self.assertEqual(thread.reply_mode, 'ai')
        self.assertEqual(thread.assigned_agent_id, self.admin.id)  # owner kept while AI replies

    def test_agent_reply_counts_as_takeover(self, provider):
        self.mock_send(provider)
        thread = self.deliver('Hi', provider)
        sent = self.client.post(self.base + f'conversations/{thread.id}/messages/',
                                {'client_id': str(uuid.uuid4()), 'content': 'Hi, this is Sam.'}, format='json')
        self.assertEqual(sent.status_code, 201)
        self.deliver('menu', provider)
        thread.refresh_from_db()
        self.assertEqual(thread.reply_mode, 'manual')
        self.assertEqual(ReplyJob.objects.filter(message__conversation=thread).count(), 1)

    def test_explicit_handoff_waits_and_customer_menu_or_admin_reactivates(self, provider):
        self.mock_send(provider)
        thread = self.deliver('I want to talk to a human', provider)
        self.assertEqual(self.last_reply(thread).content, HANDOFF)
        thread.refresh_from_db()
        self.assertEqual(thread.reply_mode, 'waiting')
        self.deliver('Are you there?', provider)
        self.assertEqual(len(self.sent_texts(provider)), 1)
        thread = self.deliver('menu', provider)
        thread.refresh_from_db()
        self.assertEqual(thread.reply_mode, 'ai')
        self.assertIn('1. View Products', self.last_reply(thread).content)
        # A previous handoff does not block an admin-reactivated conversation either.
        self.deliver('human please', provider)
        self.assertEqual(self.client.patch(self.base + f'conversations/{thread.id}/', {'reply_mode': 'ai'}, format='json').status_code, 200)
        self.assertIn('1. Alpha Plan', self.last_reply(self.deliver('1', provider)).content)

    def test_ordinary_messages_never_hand_off(self, provider):
        self.mock_send(provider)
        for body in ('Okey', 'Product', 'I need to know some details', 'any other services'):
            reply = self.last_reply(self.deliver(body, provider)).content
            self.assertNotEqual(reply, HANDOFF, body)
            self.assertIn('1. View Products', reply, body)
        thread = self.deliver('photo', provider, type='image')
        self.assertIn('only read text', self.last_reply(thread).content)
        thread.refresh_from_db()
        self.assertEqual(thread.reply_mode, 'ai')
        self.assertFalse(ReplyJob.objects.filter(state='handoff').exists())
        # After the fallback menu, numbers are interpreted against that main menu.
        self.assertIn('1. Alpha Plan', self.last_reply(self.deliver('1', provider)).content)

    def test_natural_language_listing_and_numbered_input_variants(self, provider):
        self.mock_send(provider)
        listing = self.last_reply(self.deliver('What products do you have?', provider)).content
        self.assertIn('1. Alpha Plan — Price: 100.00 INR', listing)
        self.assertIn('Beta Plan', self.last_reply(self.deliver('2)', provider)).content)
        self.assertIn('Reply with a number from 1 to 6', self.last_reply(self.deliver('menu', provider)).content)
        self.assertIn('Price: 250.00 INR', self.last_reply(self.deliver('prices', provider)).content)

    def test_empty_catalog_keeps_customer_on_main_menu(self, provider):
        """Live regression: after an empty product list, every number said 'not in this list'."""
        self.mock_send(provider)
        Product.objects.filter(business=self.business).delete()
        self.deliver('Hi', provider)
        empty = self.last_reply(self.deliver('1', provider)).content
        self.assertIn('catalog is currently empty', empty)
        self.assertIn('5. Business Information', empty)
        self.assertIn('business information', self.last_reply(self.deliver('5', provider)).content)
        self.assertIn('catalog is currently empty', self.last_reply(self.deliver('menu', provider) and self.deliver('4', provider)).content)
        thread = Conversation.objects.get(connection=self.connection)
        self.assertEqual(thread.chatbot_state, {'state': 'MAIN_MENU'})

    def test_whatsapp_quotation_saves_lead_without_asking_for_contact(self, provider):
        self.mock_send(provider)
        thread = None
        for body in ('Hi', '4', '1', 'Need a site for my bakery', 'Ravi'):
            thread = self.deliver(body, provider)
        reply = self.last_reply(thread).content
        self.assertIn('recorded your quotation enquiry for Alpha Plan', reply)
        from apps.products.models import ProductLead
        lead = ProductLead.objects.get(conversation=thread)
        self.assertEqual(lead.business_id, self.business.id)
        self.assertIn('bakery', lead.summary)
        self.assertNotIn('How should our team contact you', ''.join(self.sent_texts(provider)))

    def test_named_product_question_is_answered_from_workspace_catalog(self, provider):
        self.mock_send(provider)
        reply = self.last_reply(self.deliver('How much is the Beta Plan?', provider)).content
        self.assertIn('Beta Plan', reply)
        self.assertIn('250.00', reply)
        self.assertNotIn('Alpha Plan', reply)

    # -- 9: duplicate webhook deliveries ----------------------------------------------
    def test_duplicate_webhooks_produce_one_reply(self, provider):
        self.mock_send(provider)
        payload = self.payload('Hi', message_id='false_dup_key')
        self.assertFalse(self.post_webhook(payload).data['duplicate'])
        self.assertTrue(self.post_webhook(payload).data['duplicate'])
        nokey = self.payload('Hello', message_id=False)  # WA-JS left id unserialized
        self.assertFalse(self.post_webhook(nokey).data['duplicate'])
        self.assertTrue(self.post_webhook(nokey).data['duplicate'])
        for event in WebhookEvent.objects.all():
            process_webhook(str(self.business.id), str(event.id))
            process_webhook(str(self.business.id), str(event.id))
        thread = Conversation.objects.get(connection=self.connection)
        for _ in range(3):
            run_replies(str(self.business.id), str(thread.id))
            dispatch(str(self.business.id), str(thread.id))
        self.assertEqual(thread.messages.filter(direction='inbound').count(), 2)
        self.assertEqual(ReplyJob.objects.count(), 2)
        self.assertEqual(provider.return_value.send_text.call_count, 2)

    def test_message_without_serialized_id_is_processed(self, provider):
        """Regression: WhatsApp Web builds where WA-JS drops msg.id._serialized."""
        self.mock_send(provider)
        thread = self.deliver('Hi', provider, message_id=False)
        inbound = thread.messages.get(direction='inbound')
        self.assertTrue(inbound.provider_id.startswith('nokey:'))
        self.assertIn('1. View Products', self.last_reply(thread).content)
        no_timestamp = self.payload('Hi', message_id=False)
        no_timestamp.pop('t')
        self.assertEqual(self.post_webhook(no_timestamp).status_code, 400)

    def test_system_and_undecrypted_events_are_ignored(self, provider):
        for kind in ('ciphertext', 'e2e_notification', 'notification_template', 'protocol'):
            response = self.post_webhook(self.payload('', type=kind))
            self.assertEqual(response.data['reason'], 'ignored_message_type')
        self.assertFalse(WebhookEvent.objects.exists())

    # -- 10: Celery and outbound failures are recorded -------------------------------
    def test_reply_engine_failure_is_recorded_without_handoff(self, provider):
        with patch('apps.ai_engine.tasks.handle_chatbot_menu', side_effect=RuntimeError('boom 15550123456')):
            with self.assertLogs('apps.ai_engine.tasks', 'ERROR') as logs:
                thread = self.deliver('Hi', provider)
        job = ReplyJob.objects.get()
        self.assertEqual(job.state, 'failed')
        self.assertTrue(job.error.startswith('RuntimeError at '))
        self.assertNotIn('15550123456', job.error + ''.join(logs.output))
        self.assertFalse(thread.messages.filter(direction='outbound').exists())
        thread.refresh_from_db()
        self.assertEqual(thread.reply_mode, 'ai')
        errors = self.client.get(self.base + 'ai/?channel=whatsapp').data['recent_errors']
        self.assertTrue(any(row['send_state'] == 'reply_failed' for row in errors))

    def test_outbound_send_errors_are_recorded(self, provider):
        provider.return_value.send_text.side_effect = AmbiguousSend('timeout')
        reply = self.last_reply(self.deliver('Hi', provider))
        self.assertEqual(reply.send_state, 'unknown')
        self.assertIn('Delivery outcome unknown', reply.last_error)
        provider.return_value.send_text.side_effect = SafeToRetry('unreachable')
        thread = self.deliver('menu', provider)
        reply = self.last_reply(thread)
        self.assertEqual(reply.send_state, 'queued')
        self.assertEqual(reply.last_error, 'Provider unreachable before submission.')
        errors = self.client.get(self.base + 'ai/?channel=whatsapp').data['recent_errors']
        self.assertEqual({row['send_state'] for row in errors}, {'unknown', 'queued'})

    def test_disconnected_session_keeps_reply_queued_with_visible_reason(self, provider):
        self.mock_send(provider)
        thread = self.deliver('Hi', provider)  # reply submitted while connected
        Connection.objects.filter(pk=self.connection.pk).update(state='disconnected')
        Message.objects.create(business=self.business, connection=self.connection, conversation=thread,
                               direction='outbound', content='Queued', send_state='queued')
        dispatch(str(self.business.id), str(thread.id))
        queued = thread.messages.get(content='Queued')
        self.assertEqual(queued.send_state, 'queued')
        self.assertIn('not ready', queued.last_error)

    def test_broker_and_event_failures_are_logged_and_retryable(self, provider):
        task = Mock()
        task.name = 'apps.ai_engine.tasks.run_replies'
        task.delay.side_effect = ConnectionError('redis down')
        with self.assertLogs('apps.conversations.services', 'ERROR') as logs:
            enqueue(task, 'a', 'b')
        self.assertIn('celery.enqueue_failed task=apps.ai_engine.tasks.run_replies error=ConnectionError', logs.output[0])
        self.post_webhook(self.payload('Hi'))
        event = WebhookEvent.objects.get()
        with patch('apps.conversations.tasks.process_event', side_effect=RuntimeError('db')):
            with self.assertLogs('apps.conversations.tasks', 'ERROR'):
                process_webhook(str(self.business.id), str(event.id))
        event.refresh_from_db()
        self.assertEqual(event.attempts, 1)
        self.assertIsNone(event.processed_at)
        self.assertIn('RuntimeError', event.error)
        process_webhook(str(self.business.id), str(event.id))  # retry succeeds
        event.refresh_from_db()
        self.assertIsNotNone(event.processed_at)

    # -- 11: workspace isolation -------------------------------------------------------
    def test_workspace_data_stays_isolated(self, provider):
        self.mock_send(provider)
        other = Business.objects.create(name='Other shop')
        other_connection = Connection.objects.create(
            business=other, name='Other line', state='connected', desired_state='open',
            token_encrypted='mock-provider-token', ai_enabled=True,
        )
        AISettings.objects.create(business=other, enabled=True, chatbot_greeting='Welcome to Other!')
        Product.objects.create(business=other, name='Gamma Secret', price='999.00', currency='USD')
        mine = self.deliver('Hi', provider)
        theirs = self.deliver('Hi', provider, connection=other_connection)
        self.assertNotEqual(mine.id, theirs.id)
        self.assertEqual(theirs.business_id, other.id)
        self.assertIn('Welcome to Other!', self.last_reply(theirs).content)
        mine_listing = self.last_reply(self.deliver('1', provider)).content
        theirs_listing = self.last_reply(self.deliver('1', provider, connection=other_connection)).content
        self.assertNotIn('Gamma Secret', mine_listing)
        self.assertIn('Gamma Secret', theirs_listing)
        self.assertNotIn('Alpha Plan', theirs_listing)
        # A task invoked with the wrong workspace cannot touch another tenant's thread.
        before = Message.objects.count()
        run_replies(str(other.id), str(mine.id))
        self.assertEqual(Message.objects.count(), before)
        self.assertEqual(self.client.get(f'/api/workspaces/{other.id}/conversations/').status_code, 404)

    # -- 12: simulation and real WhatsApp share one reply engine -----------------------
    def test_simulation_and_real_whatsapp_produce_identical_replies(self, provider):
        self.mock_send(provider)
        started = self.client.post(self.base + 'simulation/', {'number': '+12025550149'}, format='json')
        self.assertEqual(started.status_code, 201, started.data)
        simulated = Conversation.objects.get(pk=started.data['id'])
        script = ('Hi', '1', '2', 'back', 'menu', 'Okey', '3')
        for body in script:
            self.client.post(self.base + f'simulation/{simulated.id}/', {'id': str(uuid.uuid4()), 'content': body}, format='json')
            run_replies(str(self.business.id), str(simulated.id))
            real = self.deliver(body, provider)
        sim_replies = list(simulated.messages.filter(direction='outbound').order_by('created_at', 'id').values_list('content', flat=True))
        real_replies = list(real.messages.filter(direction='outbound').order_by('created_at', 'id').values_list('content', flat=True))
        self.assertEqual(len(real_replies), len(script))
        self.assertEqual(sim_replies, real_replies)
        self.assertEqual(self.sent_texts(provider), real_replies)

    def test_reset_menu_endpoint(self, provider):
        self.mock_send(provider)
        thread = self.deliver('Hi', provider)
        self.deliver('1', provider)
        thread.refresh_from_db()
        self.assertEqual(thread.chatbot_state['state'], 'PRODUCT_LIST')
        response = self.client.post(self.base + f'conversations/{thread.id}/reset-chatbot-menu/')
        self.assertEqual(response.data['chatbot_state'], {'state': 'MAIN_MENU'})
        # '3' now means "Product Features" from the main menu, not a product number.
        reply = self.last_reply(self.deliver('3', provider)).content
        self.assertIn('1. Alpha Plan', reply)
        self.assertNotIn('Price:', reply)
