from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.products.models import Product
from apps.tenants.models import Business


class Command(BaseCommand):
    help = 'Remove only products carrying the demo_test_data marker from one selected workspace.'

    def add_arguments(self, parser):
        parser.add_argument('--workspace-id', required=True, help='UUID of the one workspace to clean.')

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            business = Business.objects.select_for_update().get(pk=options['workspace_id'])
        except (Business.DoesNotExist, ValueError):
            raise CommandError(f'Workspace {options["workspace_id"]!r} does not exist.')
        demo_products = Product.objects.filter(business=business, specifications__demo_test_data=True)
        count = demo_products.count()
        demo_products.delete()  # Cascades only to FAQs/documents belonging to these marked products.
        self.stdout.write(self.style.SUCCESS(f'Removed {count} marked demo products from workspace {business.id}.'))
