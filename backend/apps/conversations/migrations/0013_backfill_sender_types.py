from django.db import migrations


def backfill_sender_types(apps, schema_editor):
    Message = apps.get_model('conversations', 'Message')
    Message.objects.filter(direction='inbound').update(sender_type='customer')
    Message.objects.filter(direction='outbound', automated=True).update(sender_type='ai')


class Migration(migrations.Migration):
    dependencies = [('conversations', '0012_message_sender_type')]
    operations = [migrations.RunPython(backfill_sender_types, migrations.RunPython.noop)]
