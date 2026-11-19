from django.db import migrations


def backfill(apps, schema_editor):
    # Existing owned conversations are being worked on; everything else starts Unassigned.
    Conversation = apps.get_model('conversations', 'Conversation')
    Conversation.objects.filter(assigned_agent__isnull=False, crm_status='unassigned').update(crm_status='open')


def reverse(apps, schema_editor):
    pass  # crm_status is dropped by reversing 0017; nothing else changed.


class Migration(migrations.Migration):
    dependencies = [
        ('conversations', '0017_crm_status_views_followups'),
    ]

    operations = [migrations.RunPython(backfill, reverse)]
