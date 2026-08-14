from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("employees", "0002_employee_sop_worker_id_and_optional_contact"),
    ]

    operations = [
        migrations.AddField(
            model_name="employee",
            name="operation_codes",
            field=models.JSONField(blank=True, default=list, verbose_name="SOP 工序编码"),
        ),
    ]
