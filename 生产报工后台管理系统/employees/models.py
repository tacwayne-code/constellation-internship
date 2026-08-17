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
        from .sop_sync import operation_codes_for_job_title

        if self.job_title and not operation_codes_for_job_title(self.job_title):
            raise ValidationError({"job_title": "工作岗位必须填写已有工序名称，例如：组装，打包。"})


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
