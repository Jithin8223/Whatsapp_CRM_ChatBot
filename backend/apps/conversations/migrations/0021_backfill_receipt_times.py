from django.db import migrations
from django.db.models import Min


def backfill(apps, schema_editor):
    """Fill delivered_at / read_at from acknowledgements already stored. Statuses are untouched."""
    Message = apps.get_model('conversations', 'Message')
    DeliveryEvent = apps.get_model('conversations', 'DeliveryEvent')
    rows = Message.objects.filter(direction='outbound', provider_id__isnull=False, delivery_status__in=('delivered', 'read'))
    for message in rows.iterator():
        events = DeliveryEvent.objects.filter(connection_id=message.connection_id, provider_id=message.provider_id)
        delivered = events.filter(ack__gte=2, ack__lte=4).aggregate(at=Min('created_at'))['at']
        read = events.filter(ack__gte=3, ack__lte=4).aggregate(at=Min('created_at'))['at']
        if delivered or read:
            Message.objects.filter(pk=message.pk, delivered_at__isnull=True).update(delivered_at=delivered or read)
        if read:
            Message.objects.filter(pk=message.pk, read_at__isnull=True).update(read_at=read)


class Migration(migrations.Migration):
    dependencies = [('conversations', '0020_message_receipts')]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
