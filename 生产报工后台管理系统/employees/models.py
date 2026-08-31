from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models


phone_validator = RegexValidator(
    regex=r"^\+?[0-9][0-9 -]{5,19}$",
    message="请输入有效的电话号码。",
)


class Department(models.Model):
    name = models.CharField("部门名称", max_length=128, unique=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "部门"
        verbose_name_plural = "部门"
        ordering = ("name",)

    def __str__(self):
        return self.name


class JobPosition(models.Model):
    """Stable employee position used as the first level of SOP selection."""
    code = models.CharField("岗位编码", max_length=64, unique=True)
    name = models.CharField("岗位名称", max_length=128)
    is_active = models.BooleanField("是否启用", default=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "岗位"
        verbose_name_plural = "岗位"
        ordering = ("name", "code")

    def __str__(self):
        return self.name


class WorkProcess(models.Model):
    """A concrete process under a position and its non-authoritative WO rules."""
    position = models.ForeignKey(JobPosition, verbose_name="岗位", on_delete=models.PROTECT, related_name="processes")
    code = models.CharField("具体工艺编码", max_length=128, unique=True)
    name = models.CharField("具体工艺名称", max_length=128)
    is_active = models.BooleanField("是否启用", default=True)
    # Matching hints are used only to filter Odoo WOs; BOM/stock remain Odoo facts.
    wo_match_rules = models.JSONField("工单匹配规则", default=dict, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "具体工艺"
        verbose_name_plural = "具体工艺"
        ordering = ("position__name", "name", "code")

    def __str__(self):
        return f"{self.position.name} / {self.name}"


class ProcessSOP(models.Model):
    """Versioned PDF work instructions attached to one concrete process."""
    process = models.ForeignKey(WorkProcess, verbose_name="具体工艺", on_delete=models.PROTECT, related_name="sops")
    title = models.CharField("SOP 标题", max_length=255)
    version = models.CharField("版本号", max_length=64)
    pdf_file = models.FileField("PDF 文件", upload_to="process_sops/%Y/%m/")
    is_active = models.BooleanField("是否启用", default=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="上传人", null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField("上传时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "工艺 SOP"
        verbose_name_plural = "工艺 SOP"
        ordering = ("process__name", "-created_at", "-id")
        indexes = [models.Index(fields=("process", "is_active"), name="process_sop_active_idx")]

    def __str__(self):
        return f"{self.process.name} / {self.title} / {self.version}"


class EmployeeProcessAuthorization(models.Model):
    """Audited employee -> position -> process grant."""
    employee = models.ForeignKey("Employee", verbose_name="员工", on_delete=models.PROTECT, related_name="process_authorizations")
    position = models.ForeignKey(JobPosition, verbose_name="岗位", on_delete=models.PROTECT, related_name="employee_authorizations")
    process = models.ForeignKey(WorkProcess, verbose_name="具体工艺", on_delete=models.PROTECT, related_name="employee_authorizations")
    is_active = models.BooleanField("是否启用", default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="创建人", null=True, blank=True, on_delete=models.SET_NULL, related_name="created_process_authorizations")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="更新人", null=True, blank=True, on_delete=models.SET_NULL, related_name="updated_process_authorizations")
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "员工工艺授权"
        verbose_name_plural = "员工工艺授权"
        ordering = ("employee__name", "position__name", "process__name")
        constraints = [
            models.UniqueConstraint(fields=("employee", "position", "process"), name="employee_position_process_unique"),
        ]

    def clean(self):
        super().clean()
        if self.process_id and self.position_id and self.process.position_id != self.position_id:
            raise ValidationError({"process": "所选具体工艺必须属于所选岗位。"})

    def __str__(self):
        return f"{self.employee.name} / {self.position.name} / {self.process.name}"


class Employee(models.Model):
    source_worker_id = models.CharField("SOP 工人编号", max_length=64, unique=True, null=True, blank=True)
    operation_codes = models.JSONField("SOP 工序编码", default=list, blank=True)
    name = models.CharField("员工姓名", max_length=128)
    email = models.EmailField("工作电子邮件", unique=True, null=True, blank=True)
    department = models.ForeignKey(
        Department,
        verbose_name="所属部门",
        on_delete=models.PROTECT,
        related_name="employees",
    )
    job_title = models.CharField("工作岗位", max_length=128)
    phone = models.CharField("电话", max_length=20, validators=[phone_validator], blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "员工"
        verbose_name_plural = "员工"
        ordering = ("department__name", "name", "id")

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        # New staff receive explicit position/process grants. Keep the legacy
        # title field for display and migration compatibility without limiting
        # future position names to the historical operation map.


class EmployeeReportPanelAccount(models.Model):
    employee = models.OneToOneField(
        Employee,
        verbose_name="员工",
        on_delete=models.CASCADE,
        related_name="report_panel_account",
    )
    username = models.CharField("登录账号", max_length=128, unique=True)
    password_hash = models.CharField("密码哈希", max_length=128)
    is_active = models.BooleanField("启用", default=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "员工报工面板账号管理"
        verbose_name_plural = "员工报工面板账号管理"
        ordering = ("employee__department__name", "employee__name", "id")

    def __str__(self):
        return self.username

    def set_password(self, raw_password):
        self.password_hash = make_password(raw_password)

    def check_password(self, raw_password):
        return check_password(raw_password, self.password_hash)
