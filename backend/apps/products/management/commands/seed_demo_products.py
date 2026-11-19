from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.tenants.models import Business
from apps.products.models import Product, ProductFAQ


DEMO_MARKER = 'DEMO / TEST DATA'
PRODUCTS = [
    ('Business Website', '14999.00', 'Responsive business website with contact form and basic SEO', ['Responsive design', 'Contact form', 'Basic SEO'], ['business site', 'company website']),
    ('E-commerce Website', '29999.00', 'Online store with product catalog, cart and checkout integration', ['Product catalog', 'Shopping cart', 'Checkout integration'], ['ecommerce', 'online store', 'shop website']),
    ('Custom Web Application', '49999.00', 'Custom web application tailored to business requirements', ['Custom workflows', 'Business tailored design'], ['custom application', 'web app']),
    ('Website Maintenance', '2999.00', 'Website updates, basic maintenance and technical support', ['Website updates', 'Basic maintenance', 'Technical support'], ['maintenance plan', 'site support']),
]


class Command(BaseCommand):
    help = 'Add clearly marked demo products and approved FAQs to one explicitly selected workspace.'

    def add_arguments(self, parser):
        parser.add_argument('--workspace-id', required=True, help='UUID of the one workspace to seed.')

    @transaction.atomic
    def handle(self, *args, **options):
        workspace_id = options['workspace_id']
        try:
            business = Business.objects.select_for_update().get(pk=workspace_id)
        except (Business.DoesNotExist, ValueError):
            raise CommandError(f'Workspace {workspace_id!r} does not exist.')
        added = 0
        for name, price, description, features, aliases in PRODUCTS:
            product, created = Product.objects.get_or_create(
                business=business, name=name,
                defaults={
                    'description': f'{description}. {DEMO_MARKER}.',
                    'price': Decimal(price), 'currency': 'INR', 'features': features,
                    'specifications': {'aliases': aliases, 'demo_test_data': True},
                    'availability': 'in_stock', 'active': True,
                    'additional_info': f'{DEMO_MARKER}. Website project pricing; confirm requirements for a final quote.',
                },
            )
            if created:
                added += 1
            if product.specifications.get('demo_test_data') is not True:
                continue
            questions = [
                ('What products or services do you offer?', 'We offer Business Website, E-commerce Website, Custom Web Application, and Website Maintenance. Ask about a package for its configured price and features.'),
                ('What is the price of a business website?', 'The Business Website is ₹14,999. It includes a responsive business website, contact form, and basic SEO. Final quotation depends on requirements.'),
                ('Do you build e-commerce websites?', 'Yes. The E-commerce Website is ₹29,999 and includes a product catalog, cart, and checkout integration.'),
                ('What features are included?', 'Features depend on the package. The Business Website includes responsive design, contact form, and basic SEO. The E-commerce Website includes a product catalog, cart, and checkout integration.'),
                ('Do you offer website maintenance?', 'Yes. Website Maintenance is ₹2,999/month and includes website updates, basic maintenance, and technical support.'),
                ('How can I request a quotation?', 'Tell us which service you need and a little about the project. A human team member can confirm the requirements and prepare a quotation.'),
                ('Can I speak with a human agent?', 'Yes. Ask to speak with a human agent and the conversation will be handed to the team.'),
            ]
            # FAQ rows require a product FK; create each once per demo product so all
            # approved answers are available when any demo package is matched.
            for question, answer in questions:
                ProductFAQ.objects.get_or_create(
                    business=business, product=product, question=question,
                    defaults={'answer': answer, 'active': True},
                )
        self.stdout.write(self.style.SUCCESS(f'Seeded {added} demo products in workspace {business.id}. Existing products were preserved.'))
