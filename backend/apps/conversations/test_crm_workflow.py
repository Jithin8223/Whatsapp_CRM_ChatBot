"""Team CRM workflow: view -> claim -> status -> convert to lead -> follow-up -> calendar.

WhatsApp delivery is mocked; website guests use the real public widget API.
"""
import base64
import uuid
from datetime import date, timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.ai_engine.models import AISettings, ReplyJob
from apps.ai_engine.tasks import run_replies
from apps.products.models import Product, ProductLead
from apps.tenants.models import AuditEvent, Business, Membership
from apps.whatsapp.models import Connection
from .crm import VIEW_DEDUP_WINDOW
from .models import Conversation, ConversationView, FollowUp, WebhookEvent
from .services import dispatch
from .tasks import process_webhook

SECRET = 'crm-workflow-secret'
CUSTOMER = '15550123456@c.us'


@override_settings(DEBUG=True, WPPCONNECT_ENABLED=True, WPPCONNECT_WEBHOOK_SECRET=SECRET,
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'crm-workflow'}})
@patch('apps.conversations.channels.provider_for')
class CRMWorkflowTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name='Sada Water')
        self.admin = User.objects.create_user(username='amina')
        self.fadhil = User.objects.create_user(username='Fadhil')
        self.sara = User.objects.create_user(username='sara')
        for user, role in ((self.admin, 'admin'), (self.fadhil, 'agent'), (self.sara, 'agent')):
            Membership.objects.create(business=self.business, user=user, role=role)
        AISettings.objects.create(business=self.business, enabled=True)
        Product.objects.create(business=self.business, name='20L Water Can', price='120.00', currency='INR')
        self.connection = Connection.objects.create(business=self.business, name='Sales line', state='connected',
                                                    desired_state='open', token_encrypted='mock', ai_enabled=True)
        self.root = f'/api/workspaces/{self.business.id}/'
        self.auth = 'Basic ' + base64.b64encode(f'wppconnect:{SECRET}'.encode()).decode()
        self.sequence = 0

    def as_user(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def customer_says(self, text, provider, name='Rahim'):
        provider.return_value.send_text.side_effect = lambda to, body: f'out-{uuid.uuid4().hex}'
        self.sequence += 1
        APIClient().post('/internal/wppconnect/', {
            'session': self.connection.session, 'event': 'onmessage', 'type': 'chat', 'id': f'false_{uuid.uuid4().hex}',
            'from': CUSTOMER, 'body': text, 't': 1794910000 + self.sequence, 'sender': {'pushname': name},
        }, format='json', HTTP_AUTHORIZATION=self.auth)
        for event in WebhookEvent.objects.filter(processed_at__isnull=True):
            process_webhook(str(self.business.id), str(event.id))
        thread = Conversation.objects.get(connection=self.connection)
        run_replies(str(self.business.id), str(thread.id))
        dispatch(str(self.business.id), str(thread.id))
        thread.refresh_from_db()
        return thread

    # 1-4, 15: new conversation, opening shows history, viewing is recorded but never claims
    def test_new_message_viewing_and_automation_continue(self, provider):
        thread = self.customer_says('Hi', provider)
        self.assertEqual((thread.assigned_agent_id, thread.crm_status, thread.reply_mode), (None, 'unassigned', 'ai'))
        fadhil = self.as_user(self.fadhil)
        listing = fadhil.get(self.root + 'conversations/?channel=whatsapp').data['results'][0]
        self.assertEqual(listing['contact']['display_name'], 'Rahim')
        self.assertEqual(listing['crm_status'], 'unassigned')
        self.assertIn('Welcome', listing['last_message']['content'])

        self.assertEqual(fadhil.post(self.root + f'conversations/{thread.id}/view/').status_code, 204)
        history = fadhil.get(self.root + f'conversations/{thread.id}/messages/').data['results']
        self.assertEqual([row['direction'] for row in reversed(history)], ['inbound', 'outbound'])
        fadhil.post(self.root + f'conversations/{thread.id}/view/')  # UI refresh: no duplicate
        self.as_user(self.sara).post(self.root + f'conversations/{thread.id}/view/')
        views = ConversationView.objects.filter(conversation=thread)
        self.assertEqual(views.count(), 2)
        self.assertEqual(views.get(user=self.fadhil).view_count, 1)
        ConversationView.objects.filter(user=self.fadhil).update(last_viewed_at=timezone.now() - VIEW_DEDUP_WINDOW)
        fadhil.post(self.root + f'conversations/{thread.id}/view/')
        self.assertEqual(views.get(user=self.fadhil).view_count, 2)

        thread.refresh_from_db()
        self.assertIsNone(thread.assigned_agent_id)  # viewing never claims
        self.assertEqual(thread.reply_mode, 'ai')
        thread = self.customer_says('1', provider)  # automation still answers
        self.assertIn('20L Water Can', thread.messages.filter(direction='outbound').order_by('-created_at').first().content)

        # Viewing history is for administrators only.
        self.assertEqual(fadhil.get(self.root + f'conversations/{thread.id}/views/').status_code, 403)
        rows = self.as_user(self.admin).get(self.root + f'conversations/{thread.id}/views/').data
        self.assertEqual({row['username'] for row in rows}, {'Fadhil', 'sara'})

    # 5, 6, 15, 19: claim, header name, conflicts, admin reassignment; AI keeps replying
    def test_claim_assigns_without_disabling_ai(self, provider):
        thread = self.customer_says('Hi', provider)
        fadhil, sara = self.as_user(self.fadhil), self.as_user(self.sara)
        claimed = fadhil.post(self.root + f'conversations/{thread.id}/claim/')
        self.assertEqual(claimed.status_code, 200)
        self.assertEqual((claimed.data['assigned_agent_name'], claimed.data['crm_status'], claimed.data['reply_mode']),
                         ('Fadhil', 'open', 'ai'))
        self.assertEqual(sara.post(self.root + f'conversations/{thread.id}/claim/').status_code, 409)
        self.assertEqual(sara.patch(self.root + f'conversations/{thread.id}/', {'assigned_agent': self.sara.id}, format='json').status_code, 409)
        self.assertEqual(sara.patch(self.root + f'conversations/{thread.id}/', {'assigned_agent': self.admin.id}, format='json').status_code, 403)
        self.assertTrue(AuditEvent.objects.filter(action='conversation.claimed', actor=self.fadhil).exists())
        thread = self.customer_says('1', provider)
        self.assertEqual(ReplyJob.objects.filter(message__conversation=thread, state='answered').count(), 2)

        reassigned = self.as_user(self.admin).patch(self.root + f'conversations/{thread.id}/', {'assigned_agent': self.sara.id}, format='json')
        self.assertEqual(reassigned.data['assigned_agent_name'], 'sara')
        self.assertEqual(reassigned.data['reply_mode'], 'ai')
        self.assertEqual(fadhil.patch(self.root + f'conversations/{thread.id}/', {'crm_status': 'open'}, format='json').status_code, 403)

    # 7-9: status dropdown, conversion creates exactly one lead, leaving Converted keeps it
    def test_status_changes_and_idempotent_conversion(self, provider):
        thread = self.customer_says('Hi', provider)
        thread.chatbot_state = {'state': 'PRODUCT_DETAILS', 'product_id': str(Product.objects.get().id)}
        thread.save(update_fields=['chatbot_state'])
        fadhil = self.as_user(self.fadhil)
        fadhil.post(self.root + f'conversations/{thread.id}/claim/')
        url = self.root + f'conversations/{thread.id}/'
        for value in ('unanswered', 'not_interested'):
            self.assertEqual(fadhil.patch(url, {'crm_status': value}, format='json').data['crm_status'], value)
        self.assertEqual(fadhil.patch(url, {'crm_status': 'bogus'}, format='json').status_code, 400)
        for _ in range(2):
            self.assertEqual(fadhil.patch(url, {'crm_status': 'converted'}, format='json').status_code, 200)
        lead = ProductLead.objects.get(conversation=thread)
        self.assertEqual((lead.status, lead.business_id, lead.assigned_agent_id), ('converted', self.business.id, self.fadhil.id))
        self.assertEqual(lead.product.name, '20L Water Can')
        self.assertIsNotNone(lead.converted_at)
        leads = fadhil.get(self.root + 'leads/?status=converted&source=whatsapp').data['results']
        self.assertEqual(len(leads), 1)
        self.assertEqual((leads[0]['contact_name'], leads[0]['assigned_agent_name'], leads[0]['source']), ('Rahim', 'Fadhil', 'whatsapp'))
        fadhil.patch(url, {'crm_status': 'open'}, format='json')
        self.assertTrue(ProductLead.objects.filter(conversation=thread, status='converted').exists())  # kept
        self.assertEqual(ProductLead.objects.count(), 1)
        self.assertEqual(fadhil.patch(self.root + f'leads/{lead.id}/', {'priority': 'high'}, format='json').data['priority'], 'high')
        self.assertEqual(fadhil.get(self.root + 'leads/?priority=high&assignee=me').data['count'], 1)
        self.assertEqual(fadhil.get(self.root + 'leads/?search=water').data['count'], 1)

    # 10-13: follow-up modal data, notes, calendar date, done/reopen history, overdue
    def test_followup_notes_calendar_and_history(self, provider):
        thread = self.customer_says('Hi', provider)
        fadhil = self.as_user(self.fadhil)
        fadhil.post(self.root + f'conversations/{thread.id}/claim/')
        due = date(2026, 12, 3)
        created = fadhil.post(self.root + 'followups/', {
            'conversation': str(thread.id), 'customer_said': 'Needs 40 cans weekly for the office.',
            'team_said': 'Quoted INR 120 per can; free delivery above 30 cans.', 'next_action': 'send_quotation',
            'next_steps': 'Email the formal quotation.', 'due_date': due.isoformat(), 'due_time': '10:30', 'priority': 'high',
        }, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual((created.data['contact_name'], created.data['assigned_agent_name'], created.data['channel']),
                         ('Rahim', 'Fadhil', 'whatsapp'))
        self.assertEqual(fadhil.post(self.root + 'followups/', {'conversation': str(thread.id), 'due_date': '2026-12-04'},
                                     format='json').status_code, 400)  # notes required
        detail = fadhil.get(self.root + f'conversations/{thread.id}/').data
        self.assertEqual(detail['followups']['pending'], 1)
        month = fadhil.get(self.root + 'followups/?date_from=2026-12-01&date_to=2026-12-31').data
        self.assertEqual([(row['due_date'], row['contact_name']) for row in month], [('2026-12-03', 'Rahim')])
        self.assertEqual(fadhil.get(self.root + 'followups/?date_from=2026-11-01&date_to=2026-11-30').data, [])

        item = created.data['id']
        moved = fadhil.patch(self.root + f'followups/{item}/', {'due_date': '2026-12-05'}, format='json')
        self.assertEqual(moved.data['due_date'], '2026-12-05')
        self.assertTrue(fadhil.get(self.root + 'followups/?status=overdue&today=2026-12-06').data['results'][0]['overdue'])
        done = fadhil.post(self.root + f'followups/{item}/complete/').data
        self.assertEqual((done['status'], done['completed_by_name']), ('done', 'Fadhil'))
        self.assertEqual(fadhil.get(self.root + 'followups/?status=overdue&today=2026-12-06').data['count'], 0)
        self.assertEqual(fadhil.get(self.root + 'followups/?status=done').data['count'], 1)  # history kept
        self.assertEqual(fadhil.post(self.root + f'followups/{item}/reopen/').data['status'], 'pending')
        actions = [row['action'] for row in fadhil.get(self.root + f'followups/{item}/history/').data]
        self.assertEqual(actions, ['created', 'rescheduled', 'completed', 'reopened'])
        # Only the assignee, the creator or an admin may change it.
        self.assertEqual(self.as_user(self.sara).post(self.root + f'followups/{item}/complete/').status_code, 403)
        self.assertEqual(self.as_user(self.admin).post(self.root + f'followups/{item}/complete/').status_code, 200)
        fadhil.patch(self.root + f'conversations/{thread.id}/', {'crm_status': 'converted'}, format='json')
        lead_followup = fadhil.post(self.root + 'followups/', {'conversation': str(thread.id), 'next_steps': 'Confirm delivery day',
                                                                'due_date': '2026-12-10'}, format='json').data
        self.assertIsNotNone(lead_followup['lead'])
        self.assertEqual(fadhil.get(self.root + 'followups/?source=lead').data['count'], 1)
        self.assertEqual(fadhil.get(self.root + 'followups/?source=website').data['count'], 0)
        counts = fadhil.get(self.root + 'dashboard/?today=2026-12-11').data
        self.assertEqual((counts['followups_overdue'], counts['followups_due_today']), (1, 0))

    # 16: human takeover stops automation and cancels a pending reply; resume restores it
    def test_takeover_and_resume_are_explicit(self, provider):
        thread = self.customer_says('Hi', provider)
        fadhil = self.as_user(self.fadhil)
        fadhil.post(self.root + f'conversations/{thread.id}/claim/')
        pending = thread.messages.create(business=self.business, connection=self.connection, direction='outbound',
                                         content='queued bot reply', automated=True, sender_type='ai', send_state='queued')
        taken = fadhil.post(self.root + f'conversations/{thread.id}/takeover/')
        self.assertEqual((taken.data['reply_mode'], taken.data['assigned_agent_name']), ('manual', 'Fadhil'))
        pending.refresh_from_db()
        self.assertEqual(pending.send_state, 'cancelled')
        thread = self.customer_says('1', provider)
        self.assertEqual(ReplyJob.objects.filter(message__conversation=thread).count(), 1)
        self.assertEqual(thread.chatbot_state.get('state'), 'MAIN_MENU')  # menu state preserved
        resumed = fadhil.post(self.root + f'conversations/{thread.id}/resume-ai/')
        self.assertEqual((resumed.data['reply_mode'], resumed.data['assigned_agent_name']), ('ai', 'Fadhil'))
        thread = self.customer_says('1', provider)
        self.assertIn('20L Water Can', thread.messages.filter(direction='outbound').order_by('-created_at').first().content)
        self.assertEqual(self.as_user(self.sara).post(self.root + f'conversations/{thread.id}/takeover/').status_code, 403)

    # 14: website conversations support the same workflow
    def test_website_conversation_full_workflow(self, provider):
        admin = self.as_user(self.admin)
        key = admin.patch(self.root + 'widget/', {'title': 'Help', 'welcome': 'Hi', 'color': '#123b30',
                                                  'origins': ['https://shop.example'], 'enabled': True}, format='json').data['public_key']
        guest = APIClient(HTTP_ORIGIN='https://shop.example', REMOTE_ADDR=f'g-{uuid.uuid4()}')
        token = guest.post(f'/api/guest/{key}/').data['token']
        guest.post(f'/api/guest/{key}/messages/', {'id': str(uuid.uuid4()), 'content': 'Hi'}, format='json', HTTP_AUTHORIZATION=f'Bearer {token}')
        thread = Conversation.objects.get(connection__provider='website')
        run_replies(str(self.business.id), str(thread.id))
        fadhil = self.as_user(self.fadhil)
        fadhil.post(self.root + f'conversations/{thread.id}/view/')
        claimed = fadhil.post(self.root + f'conversations/{thread.id}/claim/').data
        self.assertEqual((claimed['assigned_agent_name'], claimed['reply_mode'], claimed['channel']), ('Fadhil', 'ai', 'website'))
        fadhil.patch(self.root + f'conversations/{thread.id}/', {'crm_status': 'converted'}, format='json')
        lead = fadhil.get(self.root + 'leads/?source=website').data['results'][0]
        self.assertEqual(lead['source'], 'website')
        self.assertEqual(lead['contact_phone'], '')  # no invented contact details
        self.assertTrue(lead['guest_label'].startswith('Guest #'))
        followup = fadhil.post(self.root + 'followups/', {'conversation': str(thread.id), 'customer_said': 'Asked about bulk pricing',
                                                          'due_date': '2026-12-01'}, format='json').data
        self.assertEqual(followup['channel'], 'website')
        self.assertEqual(fadhil.post(self.root + f'conversations/{thread.id}/takeover/').data['reply_mode'], 'manual')
        reply = fadhil.post(self.root + f'conversations/{thread.id}/messages/', {'client_id': str(uuid.uuid4()), 'content': 'Sam here!'}, format='json')
        self.assertEqual(reply.status_code, 201)
        messages = guest.get(f'/api/guest/{key}/messages/', HTTP_AUTHORIZATION=f'Bearer {token}').data['messages']
        self.assertEqual(messages[-1]['content'], 'Sam here!')

    # 19: tenant isolation for every new endpoint
    def test_workflow_endpoints_are_workspace_scoped(self, provider):
        thread = self.customer_says('Hi', provider)
        other = Business.objects.create(name='Other')
        outsider = User.objects.create_user(username='outsider')
        Membership.objects.create(business=other, user=outsider, role='admin')
        client = self.as_user(outsider)
        for method, path in (('post', 'view/'), ('post', 'claim/'), ('get', 'views/'), ('post', 'takeover/')):
            self.assertEqual(getattr(client, method)(self.root + f'conversations/{thread.id}/{path}').status_code, 404)
            self.assertEqual(getattr(client, method)(f'/api/workspaces/{other.id}/conversations/{thread.id}/{path}').status_code, 404)
        self.assertEqual(client.post(f'/api/workspaces/{other.id}/followups/', {
            'conversation': str(thread.id), 'next_steps': 'x', 'due_date': '2026-12-01'}, format='json').status_code, 404)
        self.as_user(self.fadhil).post(self.root + 'followups/', {'conversation': str(thread.id), 'next_steps': 'Call',
                                                                   'due_date': '2026-12-01'}, format='json')
        self.assertEqual(client.get(f'/api/workspaces/{other.id}/followups/').data['count'], 0)
        self.assertEqual(client.get(f'/api/workspaces/{other.id}/leads/').data['count'], 0)
        self.assertEqual(APIClient().get(self.root + 'followups/').status_code, 401)
        self.assertFalse(FollowUp.objects.filter(business=other).exists())
