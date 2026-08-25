from django.contrib import admin
from django.db import transaction
from django.db.models import Count
from django.urls import reverse
from django.utils.html import format_html
from uuid import uuid4

from .forms import EmployeeCreateForm, EmployeeReportPanelAccountForm
from .models import (
    Department,
    Employee,
    EmployeeReportPanelAccount,
    EmployeeProcessAuthorization,
    JobPosition,
    WorkProcess,
)
from .sop_sync import enqueue_sop_employee_sync, operation_codes_for_job_title
from reports.models import AuditLog


class AdministratorOnlyMixin:
    """Limit employee creation and changes to Django superusers."""

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        # Historical process references must remain available for audit/reporting.
        return False


@admin.register(JobPosition)
class JobPositionAdmin(AdministratorOnlyMixin, admin.ModelAdmin):
    list_display = ("name", "is_active", "process_count", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("code", "name")
    readonly_fields = ("created_at", "updated_at")
    fields = ("name", "is_active", "created_at", "updated_at")

    @admin.display(description="具体工艺数量", ordering="process_total")
    def process_count(self, obj):
        return obj.process_total

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(process_total=Count("processes"))

    def save_model(self, request, obj, form, change):
        if not obj.code:
            obj.code = f"position-{uuid4().hex}"
        super().save_model(request, obj, form, change)
        AuditLog.objects.create(actor=request.user, action="job_position.update", target_type="JobPosition", target_id=str(obj.pk), metadata={"code": obj.code, "name": obj.name, "is_active": obj.is_active})
        employee_ids = list(obj.employee_authorizations.values_list("employee_id", flat=True).distinct())
        transaction.on_commit(lambda: [enqueue_sop_employee_sync(employee_id) for employee_id in employee_ids])


@admin.register(WorkProcess)
class WorkProcessAdmin(AdministratorOnlyMixin, admin.ModelAdmin):
    list_display = ("name", "position", "is_active", "updated_at")
    list_filter = ("is_active", "position")
    search_fields = ("code", "name", "position__code", "position__name")
    list_select_related = ("position",)
    readonly_fields = ("created_at", "updated_at")
    fieldsets = ((None, {"fields": ("position", "name", "is_active")}), ("审计信息", {"fields": ("created_at", "updated_at")}))

    def get_model_perms(self, request):
        # The process is administered from employee process authorizations.
        # Keep the model and records for the authorization and SOP permission chain.
        return {}

    def save_model(self, request, obj, form, change):
        if not obj.code:
            obj.code = f"process-{uuid4().hex}"
        super().save_model(request, obj, form, change)
        AuditLog.objects.create(actor=request.user, action="work_process.update", target_type="WorkProcess", target_id=str(obj.pk), metadata={"code": obj.code, "name": obj.name, "position": obj.position.code, "is_active": obj.is_active})
        employee_ids = list(obj.employee_authorizations.values_list("employee_id", flat=True).distinct())
        transaction.on_commit(lambda: [enqueue_sop_employee_sync(employee_id) for employee_id in employee_ids])


@admin.register(EmployeeProcessAuthorization)
class EmployeeProcessAuthorizationAdmin(AdministratorOnlyMixin, admin.ModelAdmin):
    list_display = ("employee", "position", "process", "is_active", "updated_at")
    list_filter = ("is_active", "position", "process")
    search_fields = ("employee__name", "employee__source_worker_id", "position__name", "process__name")
    list_select_related = ("employee", "position", "process")
    readonly_fields = ("created_by", "updated_by", "created_at", "updated_at")

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if "process" in form.base_fields:
            form.base_fields["process"].queryset = WorkProcess.objects.filter(is_active=True).select_related("position")
        if "position" in form.base_fields:
            form.base_fields["position"].queryset = JobPosition.objects.filter(is_active=True)
        return form

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
        transaction.on_commit(lambda employee_id=obj.employee_id: enqueue_sop_employee_sync(employee_id))
        AuditLog.objects.create(actor=request.user, action="employee_process_authorization.update", target_type="EmployeeProcessAuthorization", target_id=str(obj.pk), metadata={"employee": obj.employee_id, "position": obj.position.code, "process": obj.process.code, "is_active": obj.is_active})


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
    readonly_fields = ("department", "created_at", "updated_at")

    def get_fields(self, request, obj=None):
        if obj is None:
            return ("name", "email", "department_name", "job_title", "phone")
        return ("name", "email", "department", "job_title", "phone", "created_at", "updated_at")

    def get_model_perms(self, request):
        # Employee records remain accessible from departments but are not a second sidebar item.
        return {}

    def get_form(self, request, obj=None, **kwargs):
        if obj is None:
            kwargs["form"] = EmployeeCreateForm
        return super().get_form(request, obj, **kwargs)

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def save_model(self, request, obj, form, change):
        if change and "job_title" in form.changed_data:
            obj.operation_codes = operation_codes_for_job_title(obj.job_title)
        super().save_model(request, obj, form, change)
        if not obj.source_worker_id:
            obj.source_worker_id = f"ADMIN_EMP_{obj.pk}"
            obj.save(update_fields=("source_worker_id", "updated_at"))
        transaction.on_commit(lambda employee_id=obj.pk: enqueue_sop_employee_sync(employee_id))


@admin.register(EmployeeReportPanelAccount)
class EmployeeReportPanelAccountAdmin(AdministratorOnlyMixin, admin.ModelAdmin):
    form = EmployeeReportPanelAccountForm
    list_display = ("username", "employee", "department", "is_active", "updated_at")
    list_filter = ("is_active", "employee__department")
    search_fields = ("username", "employee__name", "employee__department__name")
    ordering = ("employee__department__name", "employee__name")
    list_select_related = ("employee", "employee__department")
    list_per_page = 50
    fields = ("employee", "username", "is_active", "password", "password_confirmation", "created_at", "updated_at")
    readonly_fields = ("created_at", "updated_at")

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    @admin.display(description="所属部门", ordering="employee__department__name")
    def department(self, obj):
        return obj.employee.department.name
