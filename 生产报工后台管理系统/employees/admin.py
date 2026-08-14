from django.contrib import admin
from django.db import transaction
from django.db.models import Count
from django.urls import reverse
from django.utils.html import format_html

from .forms import EmployeeCreateForm
from .models import Department, Employee
from .sop_sync import enqueue_sop_employee_sync, operation_codes_for_job_title


class AdministratorOnlyMixin:
    """Limit employee creation and changes to Django superusers."""

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    change_list_template = "admin/employees/department/change_list.html"
    list_display = ("department_link", "employee_count", "created_at")
    search_fields = ("name",)
    ordering = ("name",)
    list_per_page = 50
    readonly_fields = ("name", "created_at", "updated_at")

    def has_add_permission(self, request):
        # Departments are created only when an administrator creates an employee.
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="部门")
    def department_link(self, obj):
        url = "%s?department__id__exact=%s" % (reverse("admin:employees_employee_changelist"), obj.pk)
        return format_html('<a href="{}">{}</a>', url, obj.name)

    @admin.display(description="员工数", ordering="employee_total")
    def employee_count(self, obj):
        return obj.employees.count()

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(employee_total=Count("employees"))

    def changelist_view(self, request, extra_context=None):
        context = {"can_create_employee": request.user.is_superuser}
        return super().changelist_view(request, {**(extra_context or {}), **context})


@admin.register(Employee)
class EmployeeAdmin(AdministratorOnlyMixin, admin.ModelAdmin):
    list_display = ("name", "email", "department", "job_title", "phone", "created_at")
    list_filter = ("department",)
    search_fields = ("name", "email", "job_title", "phone", "department__name")
    ordering = ("department__name", "name")
    list_per_page = 50
    readonly_fields = ("source_worker_id", "department", "created_at", "updated_at")

    def get_model_perms(self, request):
        # Employee records remain accessible from departments but are not a second sidebar item.
        return {}

    def get_form(self, request, obj=None, **kwargs):
        if obj is None:
            kwargs["form"] = EmployeeCreateForm
        return super().get_form(request, obj, **kwargs)

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if change and "job_title" in form.changed_data:
            obj.operation_codes = operation_codes_for_job_title(obj.job_title)
        super().save_model(request, obj, form, change)
        if not obj.source_worker_id:
            obj.source_worker_id = f"ADMIN_EMP_{obj.pk}"
            obj.save(update_fields=("source_worker_id", "updated_at"))
        transaction.on_commit(lambda employee_id=obj.pk: enqueue_sop_employee_sync(employee_id))
