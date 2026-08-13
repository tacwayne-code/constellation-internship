from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0002_reportsyncevent_event_key"),
    ]

    operations = [
        migrations.AddField(
            model_name="workreport",
            name="production_name",
            field=models.CharField(blank=True, max_length=128, verbose_name="生产单"),
        ),
    ]
