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

    # Localized, admin-friendly inputs for the WO matching rules. Each field is
    # optional and maps onto the JSON keys the SOP service understands; leaving
    # them all blank keeps the default BOM-component matching fallback.
    wo_match_workorder_names = forms.CharField(
        label="匹配工单名称",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="每行一个工单名称。留空表示不按工单名称过滤。",
    )
    wo_match_product_classes = forms.CharField(
        label="匹配产品类别",
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text="每行一个产品类别代码（如 machine）。留空表示不按产品类别过滤。",
    )
    wo_match_product_ids = forms.CharField(
        label="匹配产品ID",
        required=False,
        help_text="多个 ID 用英文逗号分隔。留空表示不按产品过滤。",
    )
    wo_match_workcenter_ids = forms.CharField(
        label="匹配工作中心ID",
        required=False,
        help_text="多个 ID 用英文逗号分隔。留空表示不按工作中心过滤。",
    )
    wo_match_routing_op_ids = forms.CharField(
        label="匹配工序路线操作ID",
        required=False,
        help_text="多个 ID 用英文逗号分隔。留空表示不按路线操作过滤。",
    )

    # Form field -> the JSON key the SOP service expects.
    RULE_FIELD_TO_KEY = {
        "wo_match_workorder_names": "workorderNames",
        "wo_match_product_classes": "productClasses",
        "wo_match_product_ids": "productIds",
        "wo_match_workcenter_ids": "workcenterIds",
        "wo_match_routing_op_ids": "routingOperationIds",
    }

    class Meta:
        model = WorkProcess
        fields = ("position", "name", "is_active")
        labels = {
            "position": "岗位",
            "name": "具体工艺名称",
            "is_active": "是否启用",
        }
        widgets = {
            # Offer the system's existing process names as suggestions via a
            # datalist while still letting an administrator type a new name.
            "name": forms.TextInput(attrs={"list": "process-name-options"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["position"].queryset = JobPosition.objects.filter(is_active=True).order_by("name")
        # A blank/fresh instance already carries the model default ({}), so this
        # also works for the ADD form; for EDIT it reads the stored rules.
        rules = self.instance.wo_match_rules or {}
        if not isinstance(rules, dict):
            rules = {}
        # Keep the basic fields first and the match-rule fields grouped at the end.
        ordered = {
            name: field
            for name, field in self.fields.items()
            if name not in self.RULE_FIELD_TO_KEY
        }
        for field_name in self.RULE_FIELD_TO_KEY:
            ordered[field_name] = self.fields[field_name]
        self.fields = ordered
        # Pre-populate the rule inputs from an existing process so editing keeps
        # the current matching config visible. Unexposed keys (e.g. the legacy
        # legacyOperationCode) are preserved separately during clean().
        for field_name, key in self.RULE_FIELD_TO_KEY.items():
            value = rules.get(key)
            if isinstance(value, list):
                if key in ("workorderNames", "productClasses"):
                    self.fields[field_name].initial = "\n".join(str(v) for v in value)
                else:
                    self.fields[field_name].initial = ", ".join(str(v) for v in value)

    @staticmethod
    def _line_list(text):
        return [line.strip() for line in (text or "").splitlines() if line.strip()]

    def _id_list(self, text, field_name):
        ids = []
        for part in (text or "").split(","):
            part = part.strip()
            if not part:
                continue
            try:
                ids.append(int(part))
            except ValueError:
                self.add_error(field_name, "只能填写整数，多个用英文逗号分隔。")
        return ids

    def clean(self):
        cleaned = super().clean()
        # A blank/fresh instance already carries the model default ({}), so this
        # also works for the ADD form; for EDIT it reads the stored rules.
        existing = self.instance.wo_match_rules or {}
        if not isinstance(existing, dict):
            existing = {}
        # Preserve any keys we do not expose (e.g. the legacy legacyOperationCode).
        exposed = set(self.RULE_FIELD_TO_KEY.values())
        rules = {key: value for key, value in existing.items() if key not in exposed}

        names = self._line_list(cleaned.get("wo_match_workorder_names"))
        if names:
            rules["workorderNames"] = names
        classes = self._line_list(cleaned.get("wo_match_product_classes"))
        if classes:
            rules["productClasses"] = classes
        product_ids = self._id_list(cleaned.get("wo_match_product_ids"), "wo_match_product_ids")
        if product_ids:
            rules["productIds"] = product_ids
        workcenter_ids = self._id_list(cleaned.get("wo_match_workcenter_ids"), "wo_match_workcenter_ids")
        if workcenter_ids:
            rules["workcenterIds"] = workcenter_ids
        routing_op_ids = self._id_list(cleaned.get("wo_match_routing_op_ids"), "wo_match_routing_op_ids")
        if routing_op_ids:
            rules["routingOperationIds"] = routing_op_ids

        self._wo_match_rules = rules
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.wo_match_rules = getattr(self, "_wo_match_rules", {})
        if commit:
            instance.save()
        return instance
