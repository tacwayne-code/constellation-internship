from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import hashlib


def seed_legacy_authorizations(apps, schema_editor):
    Employee = apps.get_model("employees", "Employee")
    JobPosition = apps.get_model("employees", "JobPosition")
    WorkProcess = apps.get_model("employees", "WorkProcess")
    Authorization = apps.get_model("employees", "EmployeeProcessAuthorization")
    for employee in Employee.objects.all().iterator():
        names = [part.strip() for part in str(employee.job_title or "").replace("；", ",").replace("，", ",").split(",") if part.strip()]
        codes = [str(code).strip() for code in (employee.operation_codes or []) if str(code).strip()]
        if not names and not codes:
            continue
        if not names:
            names = codes
        for index, name in enumerate(names):
            position_code = "legacy-position-" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:16]
            position, _ = JobPosition.objects.get_or_create(code=position_code, defaults={"name": name, "is_active": True})
            code = codes[index] if index < len(codes) else "legacy-" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:16]
            process_code = "legacy-process-" + hashlib.sha1(code.encode("utf-8")).hexdigest()[:16]
            process, _ = WorkProcess.objects.get_or_create(code=process_code, defaults={"position_id": position.pk, "name": name, "is_active": True, "wo_match_rules": {"legacyOperationCode": code}})
            Authorization.objects.get_or_create(employee_id=employee.pk, position_id=position.pk, process_id=process.pk, defaults={"is_active": True})


def unseed_legacy_authorizations(apps, schema_editor):
    # Keep generated records on rollback if they have since acquired history.
    pass


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("employees", "0004_employee_report_panel_account"),
    ]

    operations = [
        migrations.CreateModel(
            name="JobPosition",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=64, unique=True, verbose_name="Position code")),
                ("name", models.CharField(max_length=128, verbose_name="Position name")),
                ("is_active", models.BooleanField(default=True, verbose_name="Enabled")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ("name", "code"), "verbose_name": "Job position", "verbose_name_plural": "Job positions"},
        ),
        migrations.CreateModel(
            name="WorkProcess",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=128, unique=True, verbose_name="Process code")),
                ("name", models.CharField(max_length=128, verbose_name="Process name")),
                ("is_active", models.BooleanField(default=True, verbose_name="Enabled")),
                ("wo_match_rules", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("position", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="processes", to="employees.jobposition")),
            ],
            options={"ordering": ("position__name", "name", "code"), "verbose_name": "Work process", "verbose_name_plural": "Work processes"},
        ),
        migrations.CreateModel(
            name="EmployeeProcessAuthorization",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("is_active", models.BooleanField(default=True, verbose_name="Enabled")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_process_authorizations", to=settings.AUTH_USER_MODEL)),
                ("employee", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="process_authorizations", to="employees.employee")),
                ("position", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="employee_authorizations", to="employees.jobposition")),
                ("process", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="employee_authorizations", to="employees.workprocess")),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="updated_process_authorizations", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("employee__name", "position__name", "process__name"), "verbose_name": "Employee process authorization", "verbose_name_plural": "Employee process authorizations"},
        ),
        migrations.AddConstraint(
            model_name="employeeprocessauthorization",
            constraint=models.UniqueConstraint(fields=("employee", "position", "process"), name="employee_position_process_unique"),
        ),
        migrations.RunPython(seed_legacy_authorizations, unseed_legacy_authorizations),
    ]
