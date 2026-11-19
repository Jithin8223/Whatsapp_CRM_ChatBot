"""Website chatbot end to end, plus the unified WhatsApp + website admin inbox.

Guest requests go through the public widget API exactly as widget.js sends them.
WhatsApp delivery is mocked; nothing here proves real WhatsApp delivery.
"""
import base64
import uuid
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.ai_engine.models import AISettings, KnowledgeEntry, ReplyJob
from apps.ai_engine.tasks import run_replies
from apps.ai_engine.workflows import HANDOFF
from apps.products.models import Product, ProductLead
from apps.tenants.models import Business, Membership
from apps.whatsapp.models import Connection
from .models import Conversation, WebhookEvent
from .services import dispatch
from .tasks import process_webhook

ORIGIN = 'https://shop.example'
SECRET = 'website-test-secret'


@override_settings(
    DEBUG=True, WPPCONNECT_ENABLED=True, WPPCONNECT_WEBHOOK_SECRET=SECRET,
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'website-chatbot'}},
)
class WebsiteChatbotTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='website-admin')
        self.business = Business.objects.create(name='Acme Web')
        Membership.objects.create(business=self.business, user=self.admin, role='admin')
        AISettings.objects.create(business=self.business, enabled=True)
        Product.objects.create(business=self.business, name='Business Website', price='19999.00', currency='INR',
                               description='Five-page company website.', features=['Responsive design', 'Contact form'])
        Product.objects.create(business=self.business, name='E-commerce Website', price='29999.00', currency='INR',
                               description='Online store with payments.', features=['Cart', 'Payment gateway'])
        self.admin_client = APIClient()
        self.admin_client.force_authenticate(self.admin)
        self.root = f'/api/workspaces/{self.business.id}/'
        self.guest = APIClient(HTTP_ORIGIN=ORIGIN, REMOTE_ADDR=f'guest-{uuid.uuid4()}')

    def publish_widget(self, **values):
        payload = {'title': 'Acme help', 'welcome': 'Hi there!', 'color': '#123b30', 'origins': [ORIGIN], 'enabled': True, **values}
        return self.admin_client.patch(self.root + 'widget/', payload, format='json')

    def start_guest(self):
        key = self.publish_widget().data['public_key']
        self.base = f'/api/guest/{key}/'
        token = self.guest.post(self.base).data['token']
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {token}'}
        return token

    def say(self, text):
        response = self.guest.post(self.base + 'messages/', {'id': str(uuid.uuid4()), 'content': text}, format='json', **self.auth)
        self.assertIn(response.status_code, (200, 201), response.data)
        thread = Conversation.objects.get(connection__provider='website', business=self.business)
        run_replies(str(self.business.id), str(thread.id))
        return self.guest.get(self.base + 'messages/', **self.auth).data['messages'][-1]['content']

    # -- 15. Publication and origin validation -----------------------------------------
    def test_widget_publication_requires_valid_origins(self):
        self.assertEqual(self.publish_widget(origins=[]).status_code, 400)
        bad = self.publish_widget(origins=['https://shop.example/path'])
        self.assertEqual(bad.status_code, 400)
        self.assertIn('Invalid origin', str(bad.data))
        local = self.publish_widget(origins=['http://localhost:5500', 'http://127.0.0.1:5500'])
        self.assertEqual(local.status_code, 200, local.data)
        self.assertTrue(local.data['enabled'])
        with self.settings(DEBUG=False):
            self.assertEqual(self.publish_widget(origins=['http://localhost:5500']).status_code, 400)
        key = self.publish_widget().data['public_key']
        self.assertEqual(self.guest.get(f'/api/guest/{key}/').status_code, 200)
        self.assertEqual(self.guest.get(f'/api/guest/{key}/', HTTP_ORIGIN='https://evil.example').status_code, 403)
        self.assertEqual(self.publish_widget(enabled=False).status_code, 200)
        self.assertEqual(self.guest.get(f'/api/guest/{key}/').status_code, 403)  # unpublished

    # -- 16, 17. Visitor messaging and automatic menu/product replies ------------------
    def test_visitor_menu_product_details_and_natural_questions(self):
        self.start_guest()
        config = self.guest.get(self.base).data
        self.assertEqual(config['menu'][:2], ['View Products', 'Product Prices'])
        self.assertTrue(config['automatic'])
        menu = self.say('Hi')
        self.assertIn('Welcome to our business!', menu)
        self.assertIn('Reply with a number from 1 to 6', menu)
        listing = self.say('1')
        self.assertIn('1. Business Website — Price: 19999.00 INR', listing)
        details = self.say('2)')
        for fact in ('E-commerce Website', 'Price: 29999.00 INR', 'Online store with payments.', 'Cart; Payment gateway'):
            self.assertIn(fact, details)
        self.assertIn('1. Business Website', self.say('back'))
        self.assertIn('1. View Products', self.say('menu'))
        self.assertIn('Type menu or restart', self.say('help'))
        self.assertIn('2. E-commerce Website', self.say('What products do you have?'))
        price = self.say('What is the price of a business website?')
        self.assertIn('19999.00', price)
        self.assertNotIn('29999', price)
        about = self.say('Tell me about your e-commerce website')
        self.assertIn('Online store with payments.', about)
        self.assertNotIn('Not configured', about)
        unknown = self.say('Do you repair laptops?')
        self.assertIn('could not find an answer', unknown)
        thread = Conversation.objects.get(connection__provider='website')
        self.assertEqual(thread.reply_mode, 'ai')
        self.assertFalse(ReplyJob.objects.filter(state__in=('handoff', 'failed')).exists())

    def test_approved_faq_beats_partial_product_name_match(self):
        KnowledgeEntry.objects.create(business=self.business, question='Do you provide website maintenance?',
                                      answer='Yes, monthly maintenance plans are available.')
        self.start_guest()
        self.assertEqual(self.say('Do you provide website maintenance?'), 'Yes, monthly maintenance plans are available.')

    # -- 18. Session persistence ---------------------------------------------------------
    def test_visitor_session_and_menu_state_persist(self):
        token = self.start_guest()
        self.say('Hi')
        self.say('1')
        # A reload/new tab reuses the stored token: same conversation, same menu state.
        reloaded = APIClient(HTTP_ORIGIN=ORIGIN, REMOTE_ADDR=f'guest-{uuid.uuid4()}')
        history = reloaded.get(self.base + 'messages/', HTTP_AUTHORIZATION=f'Bearer {token}').data['messages']
        self.assertEqual([row['content'] for row in history if row['direction'] == 'inbound'], ['Hi', '1'])
        self.assertIn('E-commerce Website', self.say('2'))  # still interpreted against the product list
        self.assertEqual(Conversation.objects.filter(connection__provider='website').count(), 1)
        # A different visitor gets a separate, stable identity.
        other_token = self.guest.post(self.base).data['token']
        self.assertNotEqual(other_token, token)
        guests = Conversation.objects.filter(connection__provider='website').select_related('contact')
        self.assertEqual(len({row.contact.website_visitor_id for row in guests}), 2)
        self.assertTrue(all(row.contact.guest_label for row in guests))

    # -- Quotation from the website ------------------------------------------------------
    def test_website_quotation_collects_contact_and_notifies(self):
        self.start_guest()
        for text in ('Hi', '4', '2', 'Online store for 200 items', 'Priya', '+91 98765 43210'):
            reply = self.say(text)
        self.assertIn('recorded your quotation enquiry for E-commerce Website', reply)
        self.assertIn('not a final quotation', reply)
        lead = ProductLead.objects.get(business=self.business)
        self.assertEqual(lead.product.name, 'E-commerce Website')
        self.assertIn('200 items', lead.summary)
        thread = Conversation.objects.get(connection__provider='website')
        self.assertEqual(thread.contact.crm_phone, '+919876543210')
        detail = self.admin_client.get(self.root + f'conversations/{thread.id}/').data
        self.assertEqual(detail['lead']['product'], 'E-commerce Website')
        self.assertEqual(self.admin_client.get(self.root + 'leads/').data['count'], 1)

    def test_greeting_does_not_create_a_lead(self):
        self.start_guest()
        self.say('Hi')
        self.assertFalse(ProductLead.objects.exists())

    # -- 12, 13, 14, 20. Handoff, takeover, manual replies, return to AI -----------------
    def test_handoff_takeover_manual_reply_and_return_to_ai(self):
        self.start_guest()
        self.say('Hi')
        self.assertEqual(self.guest.post(self.base + 'handoff/', **self.auth).status_code, 204)
        thread = Conversation.objects.get(connection__provider='website')
        self.assertEqual(thread.reply_mode, 'waiting')
        self.assertIsNotNone(thread.handoff_at)
        self.say('Hello?')  # explicit handoff: no automatic reply
        self.assertFalse(ReplyJob.objects.filter(message__content='Hello?').exists())

        url = self.root + f'conversations/{thread.id}/'
        sent = self.admin_client.post(url + 'messages/', {'client_id': str(uuid.uuid4()), 'content': 'Hi, Sam from Acme here.'}, format='json')
        self.assertEqual(sent.status_code, 201)
        self.assertEqual(sent.data['send_state'], 'submitted')
        thread.refresh_from_db()
        self.assertEqual((thread.reply_mode, thread.assigned_agent_id), ('manual', self.admin.id))
        history = self.guest.get(self.base + 'messages/', **self.auth).data
        self.assertEqual(history['messages'][-1]['content'], 'Hi, Sam from Acme here.')
        self.assertEqual(history['mode'], 'manual')
        self.say('menu')  # human owns it: even a menu command does not bring the bot back
        thread.refresh_from_db()
        self.assertEqual(thread.reply_mode, 'manual')

        self.assertEqual(self.admin_client.patch(url, {'reply_mode': 'ai'}, format='json').status_code, 200)
        thread.refresh_from_db()
        self.assertIsNone(thread.handoff_at)
        self.assertIn('1. Business Website', self.say('1'))

    def test_unclaimed_handoff_resumes_when_visitor_asks_for_menu(self):
        self.start_guest()
        self.assertEqual(self.say('I want to talk to a human'), HANDOFF)
        self.say('ok')
        self.assertIn('1. View Products', self.say('menu'))
        thread = Conversation.objects.get(connection__provider='website')
        self.assertEqual(thread.reply_mode, 'ai')

    # -- 19. Unified inbox ---------------------------------------------------------------
    @patch('apps.conversations.channels.provider_for')
    def test_unified_inbox_lists_both_channels_with_sources_and_state(self, provider):
        provider.return_value.send_text.side_effect = lambda to, text: f'out-{uuid.uuid4().hex}'
        connection = Connection.objects.create(business=self.business, name='Line', state='connected', desired_state='open',
                                               token_encrypted='mock', ai_enabled=True)
        auth = 'Basic ' + base64.b64encode(f'wppconnect:{SECRET}'.encode()).decode()
        APIClient().post('/internal/wppconnect/', {'session': connection.session, 'event': 'onmessage', 'id': 'wa-1',
                                                   'type': 'chat', 'from': '15550001111@c.us', 'body': 'Hi', 't': 1},
                         format='json', HTTP_AUTHORIZATION=auth)
        process_webhook(str(self.business.id), str(WebhookEvent.objects.get().id))
        whatsapp = Conversation.objects.get(connection=connection)
        run_replies(str(self.business.id), str(whatsapp.id))
        dispatch(str(self.business.id), str(whatsapp.id))
        self.start_guest()
        self.say('Hi')
        self.say('What is the price of a business website?')

        inbox = self.admin_client.get(self.root + 'conversations/').data
        self.assertEqual({row['channel'] for row in inbox['results']}, {'whatsapp', 'website'})
        self.assertEqual(self.admin_client.get(self.root + 'conversations/?channel=website').data['count'], 1)
        self.assertEqual(self.admin_client.get(self.root + 'conversations/?channel=whatsapp').data['count'], 1)
        self.assertEqual(self.admin_client.get(self.root + 'conversations/?search=15550001111').data['count'], 1)
        website = Conversation.objects.get(connection__provider='website')
        messages = self.admin_client.get(self.root + f'conversations/{website.id}/messages/').data['results']
        sources = [row['reply_source'] for row in reversed(messages) if row['automated']]
        self.assertEqual(sources, ['menu', 'knowledge'])
        detail = self.admin_client.get(self.root + f'conversations/{whatsapp.id}/').data
        self.assertEqual(detail['chatbot_state'], {'state': 'MAIN_MENU'})
        self.assertEqual(detail['reply_mode'], 'ai')
        wa_messages = self.admin_client.get(self.root + f'conversations/{whatsapp.id}/messages/').data['results']
        self.assertEqual([row['send_state'] for row in wa_messages if row['direction'] == 'outbound'], ['submitted'])

    def test_failed_reply_is_visible_on_the_conversation(self):
        self.start_guest()
        with patch('apps.ai_engine.tasks.handle_chatbot_menu', side_effect=RuntimeError('boom')):
            self.say('Hi')
        thread = Conversation.objects.get(connection__provider='website')
        failure = self.admin_client.get(self.root + f'conversations/{thread.id}/').data['reply_failure']
        self.assertTrue(failure['error'].startswith('RuntimeError'))
        self.say('Hi')  # a later successful reply clears the banner
        self.assertIsNone(self.admin_client.get(self.root + f'conversations/{thread.id}/').data['reply_failure'])

    # -- 23. Tenant isolation --------------------------------------------------------------
    def test_visitor_tokens_and_inbox_are_workspace_scoped(self):
        token = self.start_guest()
        self.say('Hi')
        other = Business.objects.create(name='Other shop')
        other_admin = User.objects.create_user(username='other-admin')
        Membership.objects.create(business=other, user=other_admin, role='admin')
        other_client = APIClient()
        other_client.force_authenticate(other_admin)
        other_key = other_client.patch(f'/api/workspaces/{other.id}/widget/', {
            'title': 'Other', 'welcome': 'Hello', 'color': '#000000', 'origins': [ORIGIN], 'enabled': True,
        }, format='json').data['public_key']
        # A token from workspace A is useless against workspace B's widget.
        self.assertEqual(self.guest.get(f'/api/guest/{other_key}/messages/', HTTP_AUTHORIZATION=f'Bearer {token}').status_code, 403)
        self.assertEqual(other_client.get(f'/api/workspaces/{other.id}/conversations/').data['count'], 0)
        thread = Conversation.objects.get(business=self.business, connection__provider='website')
        self.assertEqual(other_client.get(f'/api/workspaces/{other.id}/conversations/{thread.id}/').status_code, 404)
        self.assertEqual(other_client.get(f'/api/workspaces/{self.business.id}/conversations/').status_code, 404)
