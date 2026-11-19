from django.db import migrations, models
from django.utils import timezone


def preserve_existing_handoffs(apps, schema_editor):
    # Existing 'waiting' rows cannot be told apart from explicit escalations, so treat
    # them all as intentional: they only resume via a customer menu command or an admin.
    Conversation = apps.get_model('conversations', 'Conversation')
    Conversation.objects.filter(reply_mode='waiting', handoff_at__isnull=True).update(handoff_at=timezone.now())


class Migration(migrations.Migration):
    dependencies = [
        ('conversations', '0015_conversation_chatbot_state'),
    ]

    operations = [
        migrations.AddField(
            model_name='conversation',
            name='handoff_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(preserve_existing_handoffs, migrations.RunPython.noop),
    ]
