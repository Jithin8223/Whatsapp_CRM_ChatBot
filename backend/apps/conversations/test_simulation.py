from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.ai_engine.models import AISettings, KnowledgeEntry
from apps.ai_engine.tasks import run_replies
from apps.tenants.models import Business, Membership
from .models import Conversation
from .services import dispatch


@override_settings(DEBUG=True, WPPCONNECT_ENABLED=True)
class SimulationTests(TestCase):
    def test_formatted_phone_payload_is_normalized_and_invalid_payload_reports_field_error(self):
        owner = User.objects.create_user(username='simulation-validation-admin')
        workspace = Business.objects.create(name='Simulation validation workspace')
        Membership.objects.create(business=workspace, user=owner, role='admin')
        client = APIClient()
        client.force_authenticate(owner)
        root = f'/api/workspaces/{workspace.id}/simulation/'
        created = client.post(root, {'number': '+1 (202) 555-0149'}, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(client.post(root, {'number': 'not a number'}, format='json').status_code, 400)

    @patch('apps.conversations.channels.provider_for')
    def test_ai_manual_return_and_isolation_without_provider(self, provider):
        owner = User.objects.create_user(username='simulation-admin')
        workspace = Business.objects.create(name='Simulation workspace')
        other = Business.objects.create(name='Other workspace')
        for business in (workspace, other):
            Membership.objects.create(business=business, user=owner, role='admin')
        AISettings.objects.create(business=workspace, enabled=True)
        KnowledgeEntry.objects.create(business=workspace, question='Business hours', answer='Approved hours answer.')
        client = APIClient()
        client.force_authenticate(owner)
        root = f'/api/workspaces/{workspace.id}/'
        result = client.post(root + 'simulation/', {'number': '+12025550149'}, format='json')
        self.assertEqual(result.status_code, 201)
        thread = Conversation.objects.get(pk=result.data['id'])
        url = root + f'conversations/{thread.id}/'
        inbound = root + f'simulation/{thread.id}/'
        first = {'id': str(uuid4()), 'content': 'Hi'}
        self.assertEqual(client.post(inbound, first, format='json').status_code, 201)
        self.assertEqual(client.post(inbound, first, format='json').status_code, 200)
        run_replies(workspace.id, thread.id)
        self.assertIn('View Products', thread.messages.get(direction='outbound').content)
        listing = client.get(root + 'conversations/?channel=whatsapp').data['results'][0]
        self.assertTrue(listing['simulated'])
        self.assertEqual(listing['contact']['phone'], '+12025550149')
        self.assertEqual(client.post(f'/api/workspaces/{other.id}/simulation/{thread.id}/', first, format='json').status_code, 404)
        self.assertEqual(client.patch(url, {'reply_mode': 'manual'}, format='json').status_code, 200)
        response = client.post(url + 'messages/', {'client_id': str(uuid4()), 'content': 'How can I help you?'}, format='json')
        self.assertEqual(response.data['delivery_status'], 'simulated')
        self.assertEqual(client.post(inbound, {'id': str(uuid4()), 'content': 'A follow-up'}, format='json').status_code, 201)
        run_replies(workspace.id, thread.id)
        self.assertEqual(thread.messages.filter(direction='outbound').count(), 2)
        self.assertEqual(client.patch(url, {'reply_mode': 'ai'}, format='json').status_code, 200)
        client.post(inbound, {'id': str(uuid4()), 'content': 'What are your business hours?'}, format='json')
        run_replies(workspace.id, thread.id)
        self.assertTrue(thread.messages.filter(content='Approved hours answer.', delivery_status='simulated').exists())
        dispatch(workspace.id, thread.id)
        provider.assert_not_called()
        self.assertFalse(thread.messages.filter(send_state='queued').exists())
        with self.settings(DEBUG=False):
            self.assertEqual(client.post(root + 'simulation/', {'number': '+12025550149'}, format='json').status_code, 404)
            self.assertEqual(client.get(url).status_code, 404)
