import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("employees", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="employee",
            name="source_worker_id",
            field=models.CharField(blank=True, max_length=64, null=True, unique=True, verbose_name="SOP 工人编号"),
        ),
        migrations.AlterField(
            model_name="employee",
            name="email",
            field=models.EmailField(blank=True, max_length=254, null=True, unique=True, verbose_name="工作电子邮件"),
        ),
        migrations.AlterField(
            model_name="employee",
            name="phone",
            field=models.CharField(
                blank=True,
                max_length=20,
                validators=[django.core.validators.RegexValidator(message="请输入有效的电话号码。", regex="^\\+?[0-9][0-9 -]{5,19}$")],
                verbose_name="电话",
            ),
        ),
    ]
