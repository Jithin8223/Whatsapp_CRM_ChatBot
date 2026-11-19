from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('whatsapp', '0002_connection_ai_enabled'),
    ]

    operations = [
        migrations.AddField(
            model_name='connection',
            name='status_detail',
            field=models.CharField(blank=True, default='', max_length=200),
        ),
        migrations.AddField(
            model_name='connection',
            name='state_changed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
