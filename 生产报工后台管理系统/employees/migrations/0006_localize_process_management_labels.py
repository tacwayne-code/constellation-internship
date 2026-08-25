from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("employees", "0005_jobposition_workprocess_authorization"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="jobposition",
            options={"ordering": ("name", "code"), "verbose_name": "岗位", "verbose_name_plural": "岗位"},
        ),
        migrations.AlterModelOptions(
            name="workprocess",
            options={"ordering": ("position__name", "name", "code"), "verbose_name": "具体工艺", "verbose_name_plural": "具体工艺"},
        ),
        migrations.AlterModelOptions(
            name="employeeprocessauthorization",
            options={"ordering": ("employee__name", "position__name", "process__name"), "verbose_name": "员工工艺授权", "verbose_name_plural": "员工工艺授权"},
        ),
        migrations.AlterField(
            model_name="jobposition",
            name="code",
            field=models.CharField(max_length=64, unique=True, verbose_name="岗位编码"),
        ),
        migrations.AlterField(
            model_name="jobposition",
            name="name",
            field=models.CharField(max_length=128, verbose_name="岗位名称"),
        ),
        migrations.AlterField(
            model_name="jobposition",
            name="is_active",
            field=models.BooleanField(default=True, verbose_name="是否启用"),
        ),
        migrations.AlterField(
            model_name="jobposition",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, verbose_name="创建时间"),
        ),
        migrations.AlterField(
            model_name="jobposition",
            name="updated_at",
            field=models.DateTimeField(auto_now=True, verbose_name="更新时间"),
        ),
        migrations.AlterField(
            model_name="workprocess",
            name="position",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="processes", to="employees.jobposition", verbose_name="岗位"),
        ),
        migrations.AlterField(
            model_name="workprocess",
            name="code",
            field=models.CharField(max_length=128, unique=True, verbose_name="具体工艺编码"),
        ),
        migrations.AlterField(
            model_name="workprocess",
            name="name",
            field=models.CharField(max_length=128, verbose_name="具体工艺名称"),
        ),
        migrations.AlterField(
            model_name="workprocess",
            name="is_active",
            field=models.BooleanField(default=True, verbose_name="是否启用"),
        ),
        migrations.AlterField(
            model_name="workprocess",
            name="wo_match_rules",
            field=models.JSONField(blank=True, default=dict, verbose_name="工单匹配规则"),
        ),
        migrations.AlterField(
            model_name="workprocess",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, verbose_name="创建时间"),
        ),
        migrations.AlterField(
            model_name="workprocess",
            name="updated_at",
            field=models.DateTimeField(auto_now=True, verbose_name="更新时间"),
        ),
        migrations.AlterField(
            model_name="employeeprocessauthorization",
            name="employee",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="process_authorizations", to="employees.employee", verbose_name="员工"),
        ),
        migrations.AlterField(
            model_name="employeeprocessauthorization",
            name="position",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="employee_authorizations", to="employees.jobposition", verbose_name="岗位"),
        ),
        migrations.AlterField(
            model_name="employeeprocessauthorization",
            name="process",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="employee_authorizations", to="employees.workprocess", verbose_name="具体工艺"),
        ),
        migrations.AlterField(
            model_name="employeeprocessauthorization",
            name="is_active",
            field=models.BooleanField(default=True, verbose_name="是否启用"),
        ),
        migrations.AlterField(
            model_name="employeeprocessauthorization",
            name="created_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_process_authorizations", to=settings.AUTH_USER_MODEL, verbose_name="创建人"),
        ),
        migrations.AlterField(
            model_name="employeeprocessauthorization",
            name="updated_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="updated_process_authorizations", to=settings.AUTH_USER_MODEL, verbose_name="更新人"),
        ),
        migrations.AlterField(
            model_name="employeeprocessauthorization",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, verbose_name="创建时间"),
        ),
        migrations.AlterField(
            model_name="employeeprocessauthorization",
            name="updated_at",
            field=models.DateTimeField(auto_now=True, verbose_name="更新时间"),
        ),
    ]
