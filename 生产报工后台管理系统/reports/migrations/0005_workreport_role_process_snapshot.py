from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("reports", "0004_alter_workreport_options")]

    operations = [
        migrations.AddField("workreport", "job_role_code", models.CharField(blank=True, max_length=64, verbose_name="岗位代码")),
        migrations.AddField("workreport", "job_role_name", models.CharField(blank=True, max_length=128, verbose_name="岗位名称")),
        migrations.AddField("workreport", "process_code", models.CharField(blank=True, max_length=128, verbose_name="具体工艺代码")),
        migrations.AddField("workreport", "process_name", models.CharField(blank=True, max_length=128, verbose_name="具体工艺名称")),
    ]
