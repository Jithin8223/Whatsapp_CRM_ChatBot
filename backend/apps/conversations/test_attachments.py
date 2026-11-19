"""Private attachments, "last opened by", and follow-up deletion (WPPConnect and Supabase mocked)."""
import io
import shutil
import tempfile
import uuid
import zipfile
from unittest.mock import Mock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.ai_engine.models import AISettings
from apps.tenants.models import AuditEvent, Business, Membership
from apps.whatsapp.models import Connection
from .attachments import SupabaseStorage
from .models import Contact, Conversation, FollowUp, Message, MessageAttachment
from .services import dispatch

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64
PDF = b'%PDF-1.7\n%test\n'


def docx(macros=False):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types/>')
        archive.writestr('word/document.xml', '<w:document/>')
        if macros:
            archive.writestr('word/vbaProject.bin', 'macro')
    return buffer.getvalue()


class AttachmentTestBase(TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        settings_patch = override_settings(ATTACHMENT_ROOT=self.root, ATTACHMENT_STORAGE='local', WPPCONNECT_ENABLED=True,
                                           CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': str(uuid.uuid4())}})
        settings_patch.enable()
        self.addCleanup(settings_patch.disable)
        self.business = Business.objects.create(name='Files')
        self.admin = User.objects.create_user(username='admin-files')
        self.agent = User.objects.create_user(username='Fadhil')
        self.other_agent = User.objects.create_user(username='sara')
        for user, role in ((self.admin, 'admin'), (self.agent, 'agent'), (self.other_agent, 'agent')):
            Membership.objects.create(business=self.business, user=user, role=role)
        AISettings.objects.create(business=self.business, enabled=True)
        self.connection = Connection.objects.create(business=self.business, name='Line', state='connected', desired_state='open',
                                                    token_encrypted='mock', ai_enabled=True)
        self.contact = Contact.objects.create(business=self.business, whatsapp_id='15550001111@c.us', display_name='Rahim')
        self.thread = Conversation.objects.create(business=self.business, connection=self.connection, contact=self.contact, reply_mode='ai')
        self.root_url = f'/api/workspaces/{self.business.id}/'
        self.url = self.root_url + f'conversations/{self.thread.id}/attachments/'

    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def upload(self, client, name, data, content_type='application/octet-stream', caption='', client_id=None, url=None):
        return client.post(url or self.url, {'file': SimpleUploadedFile(name, data, content_type=content_type),
                                             'client_id': str(client_id or uuid.uuid4()), 'caption': caption}, format='multipart')


@patch('apps.conversations.channels.provider_for')
class AttachmentTests(AttachmentTestBase):
    def test_upload_stores_privately_sends_via_whatsapp_and_is_idempotent(self, provider):
        provider.return_value.send_file.return_value = 'wa-file-1'
        agent = self.client_for(self.agent)
        client_id = uuid.uuid4()
        response = self.upload(agent, 'quote.pdf', PDF, caption='Your quotation', client_id=client_id)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['attachment']['filename'], 'quote.pdf')
        self.assertNotIn('storage_key', str(response.data))
        item = MessageAttachment.objects.get()
        self.assertEqual((item.kind, item.content_type, item.size), ('pdf', 'application/pdf', len(PDF)))
        self.assertTrue(item.storage_key.startswith(f'{self.business.id}/{self.thread.id}/'))
        self.thread.refresh_from_db()
        self.assertEqual((self.thread.reply_mode, self.thread.assigned_agent_id), ('manual', self.agent.id))  # human reply
        dispatch(str(self.business.id), str(self.thread.id))
        provider.return_value.send_file.assert_called_once_with('15550001111@c.us', PDF, 'application/pdf', 'quote.pdf', 'Your quotation')
        provider.return_value.send_text.assert_not_called()
        self.assertEqual(Message.objects.get(pk=item.message_id).send_state, 'submitted')
        retry = self.upload(agent, 'quote.pdf', PDF, client_id=client_id)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(MessageAttachment.objects.count(), 1)
        dispatch(str(self.business.id), str(self.thread.id))
        self.assertEqual(provider.return_value.send_file.call_count, 1)
        self.assertTrue(AuditEvent.objects.filter(action='attachment.sent').exists())

    def test_validation_rejects_unsafe_or_mislabelled_files(self, provider):
        agent = self.client_for(self.agent)
        for name, data in (('run.exe', b'MZ' + b'\0' * 20), ('photo.png', PDF), ('logo.svg', b'<svg onload=x>'),
                           ('page.html', b'<html>'), ('macro.docx', docx(macros=True)), ('notes.txt', b'<script>x</script>'),
                           ('empty.pdf', b'')):
            response = self.upload(agent, name, data)
            self.assertEqual(response.status_code, 400, name)
        for name, data in (('photo.png', PNG), ('spec.docx', docx()), ('list.csv', b'a,b\n1,2\n')):
            self.assertEqual(self.upload(agent, name, data).status_code, 201, name)
        with self.settings(ATTACHMENT_MAX_BYTES=10):
            self.assertIn(self.upload(agent, 'big.pdf', PDF).status_code, (400, 413))
        self.assertEqual(MessageAttachment.objects.count(), 3)

    def test_download_permissions_and_headers(self, provider):
        agent = self.client_for(self.agent)
        sent = self.upload(agent, 'photo.png', PNG).data['attachment']
        download = self.url + f'{sent["id"]}/download/'
        response = agent.get(download)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, PNG)
        self.assertIn('attachment;', response['Content-Disposition'])
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertIn('sandbox', response['Content-Security-Policy'])
        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn('inline;', agent.get(download + '?inline=1')['Content-Disposition'])
        self.assertTrue(AuditEvent.objects.filter(action='attachment.downloaded').exists())
        self.assertEqual(self.client_for(self.other_agent).get(download).status_code, 200)  # members may read
        self.assertEqual(APIClient().get(download).status_code, 401)
        outsider = User.objects.create_user(username='outsider')
        other = Business.objects.create(name='Other')
        Membership.objects.create(business=other, user=outsider, role='admin')
        self.assertEqual(self.client_for(outsider).get(download).status_code, 404)
        self.assertEqual(self.client_for(outsider).get(f'/api/workspaces/{other.id}/conversations/{self.thread.id}/attachments/{sent["id"]}/download/').status_code, 404)
        listing = agent.get(self.url).data
        self.assertEqual([row['filename'] for row in listing], ['photo.png'])

    def test_agents_cannot_send_files_in_someone_elses_conversation(self, provider):
        self.thread.assigned_agent = self.agent
        self.thread.save(update_fields=['assigned_agent'])
        self.assertEqual(self.upload(self.client_for(self.other_agent), 'photo.png', PNG).status_code, 403)
        self.assertEqual(self.upload(self.client_for(self.admin), 'photo.png', PNG).status_code, 201)

    def test_storage_failure_before_send_is_retried_not_lost(self, provider):
        provider.return_value.send_file.return_value = 'wa-file-2'
        agent = self.client_for(self.agent)
        sent = self.upload(agent, 'photo.png', PNG).data
        with patch('apps.conversations.attachments.read', side_effect=__import__('rest_framework').exceptions.ValidationError('down')):
            dispatch(str(self.business.id), str(self.thread.id))
        message = Message.objects.get(pk=sent['id'])
        self.assertEqual(message.send_state, 'queued')
        provider.return_value.send_file.assert_not_called()


class WebsiteAttachmentTests(AttachmentTestBase):
    def test_website_visitor_receives_and_downloads_only_their_files(self):
        admin = self.client_for(self.admin)
        key = admin.patch(self.root_url + 'widget/', {'title': 'Help', 'welcome': 'Hi', 'color': '#123b30',
                                                       'origins': ['https://shop.example'], 'enabled': True}, format='json').data['public_key']
        guest = APIClient(HTTP_ORIGIN='https://shop.example', REMOTE_ADDR=f'g-{uuid.uuid4()}')
        token = guest.post(f'/api/guest/{key}/').data['token']
        auth = {'HTTP_AUTHORIZATION': f'Bearer {token}'}
        guest.post(f'/api/guest/{key}/messages/', {'id': str(uuid.uuid4()), 'content': 'Send the brochure'}, format='json', **auth)
        thread = Conversation.objects.get(connection__provider='website')
        sent = self.upload(admin, 'brochure.pdf', PDF, url=self.root_url + f'conversations/{thread.id}/attachments/')
        self.assertEqual(sent.status_code, 201, sent.data)
        self.assertEqual(sent.data['send_state'], 'submitted')  # website delivery is immediate
        history = guest.get(f'/api/guest/{key}/messages/', **auth).data['messages']
        attachment = history[-1]['attachment']
        self.assertEqual(attachment['filename'], 'brochure.pdf')
        file_url = f'/api/guest/{key}/attachments/{attachment["id"]}/'
        response = guest.get(file_url, **auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, PDF)
        self.assertEqual(response['Access-Control-Allow-Origin'], 'https://shop.example')
        other_token = guest.post(f'/api/guest/{key}/').data['token']
        self.assertEqual(guest.get(file_url, HTTP_AUTHORIZATION=f'Bearer {other_token}').status_code, 404)
        self.assertEqual(guest.get(file_url).status_code, 403)


class LastViewedAndFollowUpDeleteTests(AttachmentTestBase):
    def test_last_opened_by_tracks_latest_viewer_without_claiming(self):
        fadhil, sara = self.client_for(self.agent), self.client_for(self.other_agent)
        fadhil.post(self.root_url + f'conversations/{self.thread.id}/view/')
        row = fadhil.get(self.root_url + 'conversations/').data['results'][0]
        self.assertEqual((row['last_viewed_by_name'], row['assigned_agent'], row['reply_mode']), ('Fadhil', None, 'ai'))
        sara.post(self.root_url + f'conversations/{self.thread.id}/view/')
        self.assertEqual(fadhil.get(self.root_url + f'conversations/{self.thread.id}/').data['last_viewed_by_name'], 'sara')
        fadhil.post(self.root_url + f'conversations/{self.thread.id}/view/')  # quick re-open still moves "last opened by"
        self.assertEqual(sara.get(self.root_url + 'conversations/').data['results'][0]['last_viewed_by_name'], 'Fadhil')
        history = self.client_for(self.admin).get(self.root_url + f'conversations/{self.thread.id}/views/').data
        self.assertEqual({row['username'] for row in history}, {'Fadhil', 'sara'})  # history preserved

    def test_followup_counts_and_delete_permissions(self):
        fadhil, sara, admin = self.client_for(self.agent), self.client_for(self.other_agent), self.client_for(self.admin)
        create = lambda client: client.post(self.root_url + 'followups/', {'conversation': str(self.thread.id), 'next_steps': 'Call',
                                                                           'due_date': '2026-12-02'}, format='json').data['id']
        mine, other = create(fadhil), create(fadhil)
        row = fadhil.get(self.root_url + 'conversations/').data['results'][0]
        self.assertEqual((row['pending_followups'], row['next_followup_date']), (2, '2026-12-02'))
        self.assertEqual(sara.delete(self.root_url + f'followups/{mine}/').status_code, 403)
        self.assertEqual(fadhil.delete(self.root_url + f'followups/{mine}/').status_code, 204)
        fadhil.post(self.root_url + f'followups/{other}/complete/')
        self.assertEqual(fadhil.delete(self.root_url + f'followups/{other}/').status_code, 403)  # done history kept
        self.assertEqual(admin.delete(self.root_url + f'followups/{other}/').status_code, 204)
        self.assertFalse(FollowUp.objects.exists())
        self.assertEqual(AuditEvent.objects.filter(action='followup.deleted').count(), 2)


@override_settings(SUPABASE_URL='https://proj.supabase.co', SUPABASE_SERVICE_ROLE_KEY='service-role', SUPABASE_STORAGE_BUCKET='private-files')
class SupabaseStorageTests(TestCase):
    @patch('apps.conversations.attachments.httpx.request')
    def test_private_bucket_requests_are_server_side_and_authenticated(self, request):
        request.return_value = Mock(is_success=True, content=PNG)
        store = SupabaseStorage()
        store.save('biz/conv/abc', PNG, 'image/png')
        method, url = request.call_args.args
        self.assertEqual((method, url), ('POST', 'https://proj.supabase.co/storage/v1/object/private-files/biz/conv/abc'))
        self.assertEqual(request.call_args.kwargs['headers']['Authorization'], 'Bearer service-role')
        self.assertEqual(request.call_args.kwargs['headers']['x-upsert'], 'false')
        self.assertEqual(store.read('biz/conv/abc'), PNG)
        self.assertEqual(request.call_args.args[1], 'https://proj.supabase.co/storage/v1/object/authenticated/private-files/biz/conv/abc')
        request.return_value = Mock(is_success=False)
        with self.assertRaises(Exception):
            store.read('biz/conv/abc')
