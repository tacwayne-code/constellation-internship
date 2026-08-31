from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("employees", "0006_localize_process_management_labels"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProcessSOP",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=255, verbose_name="SOP 标题")),
                ("version", models.CharField(max_length=64, verbose_name="版本号")),
                ("pdf_file", models.FileField(upload_to="process_sops/%Y/%m/", verbose_name="PDF 文件")),
                ("is_active", models.BooleanField(default=True, verbose_name="是否启用")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="上传时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                ("process", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="sops", to="employees.workprocess", verbose_name="具体工艺")),
                ("uploaded_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL, verbose_name="上传人")),
            ],
            options={"verbose_name": "工艺 SOP", "verbose_name_plural": "工艺 SOP", "ordering": ("process__name", "-created_at", "-id")},
        ),
        migrations.AddIndex(model_name="processsop", index=models.Index(fields=["process", "is_active"], name="process_sop_active_idx")),
    ]
