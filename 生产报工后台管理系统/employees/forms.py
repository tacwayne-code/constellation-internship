from django import forms
from django.db import transaction

from .models import Department, Employee
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
        if not operation_codes_for_job_title(value):
            raise forms.ValidationError("工作岗位必须填写已有工序名称，例如：组装，打包。")
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
