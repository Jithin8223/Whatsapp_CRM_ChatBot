from datetime import timedelta
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.ai_engine.models import AISettings
from apps.conversations.models import Conversation


class Command(BaseCommand):
    help = 'Preview or explicitly apply a workspace’s guest chat retention policy.'

    def add_arguments(self, parser):
        parser.add_argument('workspace')
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        try:
            workspace = UUID(options['workspace'])
        except ValueError as error:
            raise CommandError('Invalid workspace identifier.') from error
        config = AISettings.objects.filter(business_id=workspace, retention_days__gt=0).first()
        if not config:
            raise CommandError('This workspace has no guest chat retention policy.')
        cutoff = timezone.now() - timedelta(days=config.retention_days)
        candidates = Conversation.objects.filter(
            business_id=workspace, connection__provider='website', contact__website_visitor_id__isnull=False,
            status='closed', last_activity_at__lt=cutoff,
        )
        if not options['apply']:
            self.stdout.write(f'{candidates.count()} closed guest conversations qualify. No records deleted.')
            return
        deleted = 0
        for conversation_id in candidates.values_list('id', flat=True).iterator():
            with transaction.atomic():
                item = candidates.select_for_update(of=('self',)).filter(pk=conversation_id).first()
                if item:
                    contact = item.contact
                    item.delete()
                    if not contact.conversations.exists():
                        contact.delete()
                    deleted += 1
        self.stdout.write(f'Deleted {deleted} closed guest conversations.')
