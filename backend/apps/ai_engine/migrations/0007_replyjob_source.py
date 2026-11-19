from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('ai_engine', '0006_replyjob_error'),
    ]

    operations = [
        migrations.AddField(
            model_name='replyjob',
            name='source',
            field=models.CharField(blank=True, default='', max_length=12),
        ),
    ]
