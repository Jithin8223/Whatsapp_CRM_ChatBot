from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('conversations', '0014_contact_email_encrypted')]
    operations = [migrations.AddField(
        model_name='conversation', name='chatbot_state',
        field=models.JSONField(blank=True, default=dict),
    )]
