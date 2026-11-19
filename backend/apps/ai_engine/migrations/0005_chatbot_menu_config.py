from django.db import migrations, models
from apps.ai_engine.models import default_chatbot_menu


class Migration(migrations.Migration):
    dependencies = [('ai_engine', '0004_replyjob_draft_content')]
    operations = [
        migrations.AddField('aisettings', 'chatbot_greeting', models.CharField(default='Welcome to our business! \U0001F44B', max_length=480)),
        migrations.AddField('aisettings', 'chatbot_menu', models.JSONField(blank=True, default=default_chatbot_menu)),
    ]
