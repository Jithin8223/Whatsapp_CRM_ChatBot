from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('ai_engine', '0005_chatbot_menu_config'),
    ]

    operations = [
        migrations.AddField(
            model_name='replyjob',
            name='error',
            field=models.CharField(blank=True, default='', max_length=160),
        ),
    ]
