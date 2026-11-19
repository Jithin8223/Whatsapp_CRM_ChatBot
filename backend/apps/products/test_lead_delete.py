"""Lead deletion: admin-only soft delete that never touches conversations, messages or follow-ups."""
import uuid

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.ai_engine.models import AISettings
from apps.conversations.models import Contact, Conversation, FollowUp, Message
from apps.tenants.models import AuditEvent, Business, Membership
from apps.whatsapp.models import Connection
from .models import Product, ProductLead


@override_settings(DEBUG=True, CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'lead-delete'}})
class LeadDeletionTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name='Shop')
        self.admin = User.objects.create_user(username='admin-del')
        self.agent = User.objects.create_user(username='agent-del')
        Membership.objects.create(business=self.business, user=self.admin, role='admin')
        Membership.objects.create(business=self.business, user=self.agent, role='agent')
        AISettings.objects.create(business=self.business, enabled=True)
        self.product = Product.objects.create(business=self.business, name='Bamboo Chair', price='125.00', currency='USD')
        self.connection = Connection.objects.create(business=self.business, name='Line', state='connected', desired_state='open', token_encrypted='x')
        self.contact = Contact.objects.create(business=self.business, whatsapp_id='15550001111@c.us', display_name='Rahim')
        self.thread = Conversation.objects.create(business=self.business, connection=self.connection, contact=self.contact)
        Message.objects.create(business=self.business, connection=self.connection, conversation=self.thread, direction='inbound',
                               content='I want the chair', provider_id='m1', sender_type='customer')
        self.root = f'/api/workspaces/{self.business.id}/'
        self.boss, self.worker = self.client_for(self.admin), self.client_for(self.agent)
        self.boss.patch(self.root + f'conversations/{self.thread.id}/', {'crm_status': 'converted'}, format='json')
        self.lead = ProductLead.objects.get(conversation=self.thread)
        self.boss.post(self.root + 'followups/', {'conversation': str(self.thread.id), 'next_steps': 'Call back', 'due_date': '2026-12-01'}, format='json')

    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_admin_soft_deletes_and_history_is_preserved(self):
        self.assertEqual(self.boss.get(self.root + 'followups/?source=lead').data['count'], 1)
        response = self.boss.delete(self.root + f'leads/{self.lead.id}/')
        self.assertEqual(response.status_code, 204)
        self.lead.refresh_from_db()
        self.assertIsNotNone(self.lead.deleted_at)
        self.assertEqual(self.lead.deleted_by, self.admin)
        self.assertEqual(self.boss.get(self.root + 'leads/').data['count'], 0)
        self.assertEqual(self.boss.get(self.root + f'leads/{self.lead.id}/').status_code, 404)
        self.assertEqual(self.boss.delete(self.root + f'leads/{self.lead.id}/').status_code, 404)
        # Conversation, messages, contact and follow-ups are untouched.
        self.thread.refresh_from_db()
        self.assertEqual(self.thread.crm_status, 'converted')
        self.assertEqual(self.thread.messages.count(), 1)
        self.assertTrue(Contact.objects.filter(pk=self.contact.pk).exists())
        self.assertEqual(FollowUp.objects.filter(conversation=self.thread).count(), 1)
        self.assertIsNone(self.boss.get(self.root + f'conversations/{self.thread.id}/').data['lead'])
        self.assertEqual(self.boss.get(self.root + 'followups/?source=lead').data['count'], 0)
        self.assertTrue(AuditEvent.objects.filter(action='lead.deleted', actor=self.admin, object_id=str(self.lead.id)).exists())

    def test_only_workspace_admins_can_delete(self):
        self.assertEqual(self.worker.delete(self.root + f'leads/{self.lead.id}/').status_code, 403)
        self.assertEqual(APIClient().delete(self.root + f'leads/{self.lead.id}/').status_code, 401)
        outsider = User.objects.create_user(username='outsider-del')
        other = Business.objects.create(name='Other')
        Membership.objects.create(business=other, user=outsider, role='admin')
        client = self.client_for(outsider)
        self.assertEqual(client.delete(self.root + f'leads/{self.lead.id}/').status_code, 404)
        self.assertEqual(client.delete(f'/api/workspaces/{other.id}/leads/{self.lead.id}/').status_code, 404)
        self.lead.refresh_from_db()
        self.assertIsNone(self.lead.deleted_at)

    def test_no_silent_recreation_but_explicit_conversion_restores(self):
        self.boss.delete(self.root + f'leads/{self.lead.id}/')
        # Passive website flow (product mention) uses get_or_create: it finds the tombstone and does nothing.
        lead, created = ProductLead.objects.get_or_create(conversation=self.thread, defaults={'business': self.business, 'product': self.product})
        self.assertFalse(created)
        self.assertEqual(self.boss.get(self.root + 'leads/').data['count'], 0)
        # Explicit re-conversion restores the same record (no duplicate).
        url = self.root + f'conversations/{self.thread.id}/'
        self.boss.patch(url, {'crm_status': 'open'}, format='json')
        self.boss.patch(url, {'crm_status': 'converted'}, format='json')
        self.assertEqual(ProductLead.objects.filter(conversation=self.thread).count(), 1)
        self.assertEqual(self.boss.get(self.root + 'leads/').data['results'][0]['id'], self.lead.id)
