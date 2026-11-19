from uuid import uuid4

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.conversations.models import Contact, Conversation
from apps.conversations.models import WebsiteWidget
from apps.ai_engine.models import AISettings
from apps.tenants.models import Business, Membership
from apps.whatsapp.models import Connection
from .models import Product, ProductFAQ, ProductLead
from .retrieval import identify_products, product_knowledge
from apps.ai_engine.providers import KnowledgeProvider
from django.core.management import call_command
from io import StringIO


class ProductAPITests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name='North Shop')
        self.other = Business.objects.create(name='South Shop')
        self.admin = User.objects.create_user(username='product-admin')
        self.agent = User.objects.create_user(username='product-agent')
        Membership.objects.create(business=self.business, user=self.admin, role='admin')
        Membership.objects.create(business=self.business, user=self.agent, role='agent')
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.base = f'/api/workspaces/{self.business.id}/'

    def test_products_are_workspace_scoped_and_admin_managed(self):
        Product.objects.create(business=self.other, name='Foreign item')
        created = self.client.post(self.base + 'products/', {
            'name': 'Travel Mug', 'description': 'Steel insulated mug', 'price': '19.95', 'currency': 'usd',
            'features': ['Keeps drinks warm'], 'specifications': {'Capacity': '400 ml'},
            'image_url': 'https://cdn.example.test/mug.png',
        }, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data['currency'], 'USD')
        self.assertEqual([item['name'] for item in self.client.get(self.base + 'products/').data['results']], ['Travel Mug'])
        self.client.force_authenticate(self.agent)
        self.assertEqual(self.client.get(self.base + 'products/').status_code, 403)
        self.assertEqual(self.client.post(self.base + 'products/', {'name': 'Nope'}, format='json').status_code, 403)

    def test_product_faq_and_document_require_workspace_product(self):
        product = Product.objects.create(business=self.business, name='Desk Lamp')
        foreign = Product.objects.create(business=self.other, name='Other Lamp')
        faq = self.client.post(self.base + 'product-faqs/', {
            'product': str(product.id), 'question': 'Does it dim?', 'answer': 'Yes, three settings.',
        }, format='json')
        self.assertEqual(faq.status_code, 201, faq.data)
        blocked = self.client.post(self.base + 'product-faqs/', {
            'product': str(foreign.id), 'question': 'Foreign?', 'answer': 'No',
        }, format='json')
        self.assertEqual(blocked.status_code, 400)
        bad = SimpleUploadedFile('notes.html', b'<script>no</script>', content_type='text/html')
        self.assertEqual(self.client.post(self.base + f'products/{product.id}/documents/', {'file': bad}).status_code, 400)
        uploaded = SimpleUploadedFile('manual.md', b'Keep away from water.', content_type='text/markdown')
        self.assertEqual(self.client.post(self.base + f'products/{product.id}/documents/', {'file': uploaded}).status_code, 201)
        self.assertEqual(self.client.get(self.base + 'products/').data['results'][0]['documents'][0]['title'], 'manual.md')

    def test_visitors_create_workspace_scoped_leads_without_personal_data(self):
        connection = Connection.objects.create(business=self.business, name='Website', provider='website', state='connected', desired_state='open')
        contact = Contact.objects.create(business=self.business, website_visitor_id=uuid4())
        conversation = Conversation.objects.create(business=self.business, connection=connection, contact=contact)
        product = Product.objects.create(business=self.business, name='Maple Desk')
        lead = ProductLead.objects.create(business=self.business, conversation=conversation, product=product, summary='Maple desk dimensions?')
        self.assertEqual(lead.product.name, 'Maple Desk')
        self.assertEqual(lead.status, 'new')
        response = self.client.get(self.base + 'leads/')
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['contact_name'], '')


class ProductRetrievalTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name='Catalog')
        self.other = Business.objects.create(name='Foreign Catalog')
        self.alpha = Product.objects.create(
            business=self.business, name='Alpha Phone', description='Compact handset.', price='299.00',
            currency='USD', availability='in_stock', warranty='One year',
        )
        self.beta = Product.objects.create(
            business=self.business, name='Beta Phone', description='Large screen handset.', price='599.00',
            currency='USD', availability='out_of_stock', warranty='Two years',
        )
        self.foreign = Product.objects.create(business=self.other, name='Alpha Phone', description='Foreign facts')

    def test_retrieval_never_crosses_workspace_or_product_context(self):
        ProductFAQ.objects.create(business=self.business, product=self.alpha, question='Alpha battery', answer='Up to 12 hours.')
        ProductFAQ.objects.create(business=self.business, product=self.beta, question='Beta battery', answer='Up to 30 hours.')
        products, entries, ambiguous = product_knowledge(self.business.id, 'What warranty does Alpha Phone have?')
        self.assertFalse(ambiguous)
        self.assertEqual([item.id for item in products], [self.alpha.id])
        combined = '\n'.join(item['answer'] for item in entries)
        self.assertIn('One year', combined)
        self.assertNotIn('Two years', combined)
        self.assertNotIn('Foreign facts', combined)

    def test_comparisons_and_ambiguous_product_names(self):
        products, entries, ambiguous = product_knowledge(self.business.id, 'Compare Alpha Phone and Beta Phone prices')
        self.assertFalse(ambiguous)
        self.assertEqual({item.id for item in products}, {self.alpha.id, self.beta.id})
        self.assertEqual(len([row for row in entries if row.get('kind') == 'product']), 2)
        comparison = KnowledgeProvider().answer('', [], entries)
        self.assertIn('Alpha Phone', comparison)
        self.assertIn('Beta Phone', comparison)
        Product.objects.create(business=self.business, name='Alpha Tablet')
        products, _, ambiguous = product_knowledge(self.business.id, 'Tell me about Alpha')
        self.assertTrue(ambiguous)
        self.assertGreaterEqual(len(products), 2)
        for number in range(8):
            Product.objects.create(business=self.business, name=f'Catalog Item {number}')
        selected, ambiguous = identify_products(self.business.id, 'Tell me about Catalog Item 7')
        self.assertFalse(ambiguous)
        self.assertEqual([item.name for item in selected], ['Catalog Item 7'])

    def test_price_and_general_catalog_questions_stay_in_workspace(self):
        products, _, ambiguous = product_knowledge(self.business.id, 'What is the cheapest website package?')
        self.assertFalse(ambiguous)
        self.assertEqual([item.id for item in products], [self.alpha.id])
        products, _, _ = product_knowledge(self.business.id, 'What services do you offer?')
        self.assertEqual({item.business_id for item in products}, {self.business.id})
        self.assertNotIn(self.foreign, products)

    def test_unknown_named_product_does_not_fall_back_to_unrelated_products(self):
        products, entries, _ = product_knowledge(self.business.id, 'How much is a Zorg Hyperdrive?')
        self.assertEqual(products, [])
        self.assertEqual(entries, [])


class DemoProductCommandTests(TestCase):
    def test_seed_is_explicit_idempotent_and_cleanup_only_removes_marked_products(self):
        business = Business.objects.create(name='Demo workspace')
        real = Product.objects.create(business=business, name='Business Website', description='Real record')
        with self.assertRaises(Exception):
            call_command('seed_demo_products', stdout=StringIO())
        call_command('seed_demo_products', '--workspace-id', str(business.id), stdout=StringIO())
        call_command('seed_demo_products', '--workspace-id', str(business.id), stdout=StringIO())
        self.assertEqual(Product.objects.filter(business=business).count(), 5)
        self.assertEqual(Product.objects.get(id=real.id).description, 'Real record')
        demo = Product.objects.get(business=business, name='E-commerce Website')
        self.assertTrue(demo.specifications['demo_test_data'])
        self.assertEqual(demo.product_faqs.filter(active=True).count(), 7)
        call_command('cleanup_demo_products', '--workspace-id', str(business.id), stdout=StringIO())
        self.assertEqual(list(Product.objects.filter(business=business).values_list('id', flat=True)), [real.id])


class ProductWidgetTests(TestCase):
    def test_public_product_chat_works_without_whatsapp_and_captures_lead(self):
        business = Business.objects.create(name='Website shop')
        connection = Connection.objects.create(
            business=business, name='Website', provider='website', state='connected', desired_state='open',
        )
        widget = WebsiteWidget.objects.create(business=business, connection=connection, enabled=True, origins=['https://shop.example'])
        product = Product.objects.create(business=business, name='Bamboo Chair', price='125.00', currency='USD', warranty='Two years')
        AISettings.objects.create(business=business, enabled=True)
        client = APIClient(HTTP_ORIGIN='https://shop.example')
        root = f'/api/guest/{widget.public_key}/'
        token = client.post(root).data['token']
        auth = {'HTTP_AUTHORIZATION': f'Bearer {token}'}
        response = client.post(root + 'messages/', {'id': str(uuid4()), 'content': 'How much is the Bamboo Chair?'}, format='json', **auth)
        self.assertEqual(response.status_code, 201)
        conversation = Conversation.objects.get(connection=connection)
        lead = ProductLead.objects.get(conversation=conversation)
        self.assertEqual(lead.product, product)
        from apps.ai_engine.tasks import run_replies
        run_replies(business.id, conversation.id)
        reply = conversation.messages.filter(direction='outbound').get()
        self.assertIn('125.00', reply.content)
        self.assertEqual(reply.sender_type, 'ai')
        self.assertFalse(connection.whatsapp_ready)
