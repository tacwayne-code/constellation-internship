from django import forms
from django.db import transaction

from .models import Department, Employee


class EmployeeCreateForm(forms.ModelForm):
    department_name = forms.CharField(label="所属部门", max_length=128)

    class Meta:
        model = Employee
        fields = ("name", "email", "department_name", "job_title", "phone")

    def clean_department_name(self):
        return self.cleaned_data["department_name"].strip()

    @transaction.atomic
    def save(self, commit=True):
        employee = super().save(commit=False)
        department, _ = Department.objects.get_or_create(name=self.cleaned_data["department_name"])
        employee.department = department
        if commit:
            employee.save()
        return employee
