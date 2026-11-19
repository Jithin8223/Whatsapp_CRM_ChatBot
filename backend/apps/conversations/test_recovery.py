import uuid
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from apps.accounts.models import User
from apps.tenants.models import Business, Membership
from apps.whatsapp.models import Connection
from .models import Contact, Conversation, Message, WebhookEvent
from .services import persist_event, process_event
from .tasks import recover_work
from .tasks import process_webhook


class RecoveryTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name='Recovery')
        self.connection = Connection.objects.create(business=self.business, name='Support')
        self.contact = Contact.objects.create(business=self.business, whatsapp_id='919999999999@c.us')
        self.conversation = Conversation.objects.create(business=self.business, connection=self.connection, contact=self.contact)

    def test_out_of_order_tasks_preserve_ingress_order(self):
        for number in (1, 2):
            persist_event(self.connection, {'event': 'onmessage', 'id': f'm{number}', 'from': self.contact.whatsapp_id, 'body': f'Message {number}'})
        first, second = WebhookEvent.objects.order_by('created_at', 'id')
        process_event(self.business.id, second.id)
        self.assertFalse(Message.objects.exists())
        process_event(self.business.id, first.id)
        process_event(self.business.id, second.id)
        self.assertEqual(list(Message.objects.order_by('created_at').values_list('content', flat=True)), ['Message 1', 'Message 2'])

    def test_worker_drains_earlier_events_without_waiting_for_sweeper(self):
        for number in (1, 2):
            persist_event(self.connection, {'event': 'onmessage', 'id': f'm{number}', 'from': self.contact.whatsapp_id, 'body': str(number)})
        last = WebhookEvent.objects.order_by('created_at', 'id').last()
        process_webhook(str(self.business.id), str(last.id))
        self.assertEqual(list(Message.objects.order_by('created_at').values_list('content', flat=True)), ['1', '2'])

    @patch('apps.conversations.tasks.enqueue')
    def test_stale_send_is_held_without_resubmission(self, enqueue):
        item = Message.objects.create(business=self.business, connection=self.connection, conversation=self.conversation, direction='outbound', content='Interrupted', send_state='sending', sending_at=timezone.now() - timedelta(minutes=3))
        recover_work()
        item.refresh_from_db()
        self.assertEqual(item.send_state, 'unknown')
        enqueue.assert_not_called()

    @patch('apps.conversations.services.enqueue')
    def test_ack_is_durable_before_broker_publish(self, enqueue):
        with self.captureOnCommitCallbacks(execute=True):
            persist_event(self.connection, {'event': 'onack', 'id': 'out', 'ack': 2})
            self.assertTrue(WebhookEvent.objects.exists())
            enqueue.assert_not_called()
        enqueue.assert_called_once()
