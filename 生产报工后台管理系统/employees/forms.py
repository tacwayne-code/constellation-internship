from django import forms
from django.db import transaction

from .models import Department, Employee, EmployeeReportPanelAccount, JobPosition, WorkProcess
from .sop_sync import operation_codes_for_job_title


class EmployeeCreateForm(forms.ModelForm):
    department_name = forms.CharField(label="所属部门", max_length=128)

    class Meta:
        model = Employee
        fields = ("name", "email", "department_name", "job_title", "phone")

    def clean_department_name(self):
        return self.cleaned_data["department_name"].strip()

    def clean_job_title(self):
        value = self.cleaned_data["job_title"].strip()
        if not value:
            raise forms.ValidationError("工作岗位不能为空。")
        return value

    @transaction.atomic
    def save(self, commit=True):
        employee = super().save(commit=False)
        department, _ = Department.objects.get_or_create(name=self.cleaned_data["department_name"])
        employee.department = department
        employee.operation_codes = operation_codes_for_job_title(employee.job_title)
        if commit:
            employee.save()
        return employee


class EmployeeReportPanelAccountForm(forms.ModelForm):
    password = forms.CharField(
        label="密码",
        required=False,
        strip=False,
        widget=forms.PasswordInput(render_value=False),
    )
    password_confirmation = forms.CharField(
        label="确认密码",
        required=False,
        strip=False,
        widget=forms.PasswordInput(render_value=False),
    )

    class Meta:
        model = EmployeeReportPanelAccount
        fields = ("employee", "username", "is_active")

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get("password")
        confirmation = cleaned_data.get("password_confirmation")
        if self.instance._state.adding and not password:
            self.add_error("password", "创建账号时必须设置密码。")
        if password or confirmation:
            if password != confirmation:
                self.add_error("password_confirmation", "两次输入的密码不一致。")
        return cleaned_data

    def save(self, commit=True):
        account = super().save(commit=False)
        password = self.cleaned_data.get("password")
        if password:
            account.set_password(password)
        if commit:
            account.save()
        return account


class WorkProcessManagementForm(forms.ModelForm):
    """Process maintenance form embedded under employee process authorization."""

    class Meta:
        model = WorkProcess
        fields = ("position", "name", "is_active", "wo_match_rules")
        labels = {
            "position": "岗位",
            "name": "具体工艺名称",
            "is_active": "是否启用",
            "wo_match_rules": "工单匹配规则",
        }
        help_texts = {
            "wo_match_rules": "留空或填写 {} 表示沿用现有 BOM 组件匹配；可配置 routingOperationIds、productIds、productClasses、workcenterIds 或 workorderNames。",
        }
        widgets = {
            "wo_match_rules": forms.Textarea(attrs={"rows": 5}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["position"].queryset = JobPosition.objects.filter(is_active=True).order_by("name")

    def clean_wo_match_rules(self):
        rules = self.cleaned_data.get("wo_match_rules")
        if rules in (None, ""):
            return {}
        if not isinstance(rules, dict):
            raise forms.ValidationError("工单匹配规则必须是 JSON 对象。")
        return rules
