from django.contrib import admin
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils import timezone
from uuid import uuid4

from .forms import EmployeeCreateForm, EmployeeReportPanelAccountForm, ProcessSOPUploadForm, WorkProcessManagementForm
from .models import (
    Department,
    Employee,
    EmployeeReportPanelAccount,
    EmployeeProcessAuthorization,
    JobPosition,
    ProcessSOP,
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
    change_list_template = "admin/employees/jobposition/change_list.html"
    # The action checkbox column is auto-prepended by the changelist when a
    # delete action is registered; listing "action_checkbox" here would render
    # a second checkbox column, so it is intentionally omitted.
    list_display = ("name", "is_active", "process_count", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("code", "name")
    readonly_fields = ("created_at", "updated_at")
    fields = ("name", "is_active", "created_at", "updated_at")
    # A custom "delete selected" action is registered (and kept visible even
    # though the model disables raw delete) so the changelist renders its
    # selection checkboxes; the standalone toolbar button reuses the same logic.
    actions = ("delete_selected_positions",)

    def _cascade_delete_positions(self, request, positions):
        """Delete the given positions together with their processes and grants."""
        processes = WorkProcess.objects.filter(position__in=positions)
        employee_ids = list(
            EmployeeProcessAuthorization.objects.filter(process__in=processes)
            .values_list("employee_id", flat=True).distinct()
        )
        position_details = list(positions.values("id", "code", "name"))
        EmployeeProcessAuthorization.objects.filter(process__in=processes).delete()
        ProcessSOP.objects.filter(process__in=processes).delete()
        processes.delete()
        positions.delete()
        AuditLog.objects.create(
            actor=request.user,
            action="job_position.delete",
            target_type="JobPosition",
            target_id=",".join(str(item["id"]) for item in position_details),
            metadata={"positions": position_details},
        )
        transaction.on_commit(
            lambda: [enqueue_sop_employee_sync(employee_id) for employee_id in set(employee_ids)]
        )
        return len(position_details)

    @admin.action(description="删除选中的岗位", permissions=["change"])
    def delete_selected_positions(self, request, queryset):
        if not request.user.is_superuser:
            self.message_user(request, "当前账号没有岗位删除权限。", level="error")
            return
        with transaction.atomic():
            count = self._cascade_delete_positions(request, queryset)
        self.message_user(request, f"已删除 {count} 个岗位及其相关工艺。", messages.SUCCESS)

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "delete-selected/",
                self.admin_site.admin_view(self.delete_selected_view),
                name="employees_jobposition_delete_selected",
            ),
        ]
        return custom_urls + urls

    def delete_selected_view(self, request):
        if not request.user.is_superuser:
            raise PermissionDenied
        if request.method != "POST":
            return redirect("admin:employees_jobposition_changelist")
        position_ids = request.POST.getlist("position_ids")
        positions = JobPosition.objects.filter(pk__in=position_ids)
        if not positions.exists():
            self.message_user(request, "请先选择需要删除的岗位。", messages.WARNING)
            return redirect("admin:employees_jobposition_changelist")

        with transaction.atomic():
            count = self._cascade_delete_positions(request, positions)
        self.message_user(request, f"已删除 {count} 个岗位及其相关工艺。", messages.SUCCESS)
        return redirect("admin:employees_jobposition_changelist")

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


@admin.register(ProcessSOP)
class ProcessSOPAdmin(AdministratorOnlyMixin, admin.ModelAdmin):
    list_display = ("title", "process", "version", "is_active", "created_at")
    list_filter = ("is_active", "process__position")
    search_fields = ("title", "version", "process__name", "process__code")
    list_select_related = ("process", "uploaded_by")
    readonly_fields = ("created_at", "updated_at", "uploaded_by")

    def get_model_perms(self, request):
        # SOPs are maintained inside the concrete-process editor. Keep the
        # model registered for ORM/API use without exposing a duplicate menu.
        return {}

    def save_model(self, request, obj, form, change):
        if not change:
            obj.uploaded_by = request.user
        if obj.is_active:
            ProcessSOP.objects.filter(process=obj.process, title=obj.title, is_active=True).exclude(pk=obj.pk).update(is_active=False)
        super().save_model(request, obj, form, change)


@admin.register(EmployeeProcessAuthorization)
class EmployeeProcessAuthorizationAdmin(AdministratorOnlyMixin, admin.ModelAdmin):
    change_list_template = "admin/employees/employeeprocessauthorization/change_list.html"
    list_display = ("action_checkbox", "employee", "position", "process", "status_indicator", "updated_at")
    list_filter = ("is_active", "position", "process")
    search_fields = ("employee__name", "employee__source_worker_id", "position__name", "process__name")
    list_select_related = ("employee", "position", "process")
    readonly_fields = ("created_by", "updated_by", "created_at", "updated_at")

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "processes/",
                self.admin_site.admin_view(self.process_management_view),
                name="employees_employeeprocessauthorization_processes",
            ),
            path(
                "processes/add/",
                self.admin_site.admin_view(self.process_add_view),
                name="employees_employeeprocessauthorization_process_add",
            ),
            path(
                "processes/<int:process_id>/change/",
                self.admin_site.admin_view(self.process_change_view),
                name="employees_employeeprocessauthorization_process_change",
            ),
            path(
                "processes/delete/",
                self.admin_site.admin_view(self.process_delete_view),
                name="employees_employeeprocessauthorization_process_delete",
            ),
            path(
                "delete-selected/",
                self.admin_site.admin_view(self.authorization_delete_view),
                name="employees_employeeprocessauthorization_delete_selected",
            ),
        ]
        return custom_urls + urls

    def _require_superuser(self, request):
        if not request.user.is_superuser:
            raise PermissionDenied

    def _process_management_context(self, request, **extra):
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": "员工工艺授权 - 工艺管理",
            "processes": WorkProcess.objects.select_related("position").prefetch_related("sops").order_by("position__name", "name"),
            "process_name_options": list(
                WorkProcess.objects.filter(is_active=True).values_list("name", flat=True).distinct().order_by("name")
            ),
        }
        context.update(extra)
        return context

    @admin.display(description="是否启用", boolean=True)
    def status_indicator(self, obj):
        """Render with Django's native green/red circular status icons."""
        return obj.is_active

    @staticmethod
    def _enqueue_employee_syncs(employee_ids):
        unique_ids = tuple(set(employee_ids))
        transaction.on_commit(lambda: [enqueue_sop_employee_sync(employee_id) for employee_id in unique_ids])

    def _sync_process_status_to_authorizations(self, process, actor):
        """A process and every grant for it expose one shared enabled status."""
        authorizations = EmployeeProcessAuthorization.objects.filter(process=process)
        employee_ids = list(authorizations.values_list("employee_id", flat=True).distinct())
        authorizations.update(
            is_active=process.is_active,
            updated_by=actor,
            updated_at=timezone.now(),
        )
        self._enqueue_employee_syncs(employee_ids)

    def process_management_view(self, request):
        self._require_superuser(request)
        return TemplateResponse(
            request,
            "admin/employees/employeeprocessauthorization/process_management.html",
            self._process_management_context(request),
        )

    def process_delete_view(self, request):
        self._require_superuser(request)
        if request.method != "POST":
            return redirect("admin:employees_employeeprocessauthorization_processes")
        process_ids = request.POST.getlist("process_ids")
        processes = WorkProcess.objects.filter(pk__in=process_ids).prefetch_related("employee_authorizations", "sops")
        if not processes.exists():
            self.message_user(request, "请先选择需要删除的工艺。", messages.WARNING)
            return redirect("admin:employees_employeeprocessauthorization_processes")

        with transaction.atomic():
            employee_ids = list(
                EmployeeProcessAuthorization.objects.filter(process__in=processes)
                .values_list("employee_id", flat=True).distinct()
            )
            deleted_count = processes.count()
            process_details = list(processes.values("id", "code", "name"))
            # These records are owned by a process. Removing them first makes
            # the administrator's explicit deletion intent work with PROTECT FKs.
            EmployeeProcessAuthorization.objects.filter(process__in=processes).delete()
            ProcessSOP.objects.filter(process__in=processes).delete()
            processes.delete()
            AuditLog.objects.create(
                actor=request.user,
                action="work_process.delete",
                target_type="WorkProcess",
                target_id=",".join(str(item["id"]) for item in process_details),
                metadata={"processes": process_details},
            )
            self._enqueue_employee_syncs(employee_ids)
        self.message_user(request, f"已删除 {deleted_count} 个工艺及其相关授权。", messages.SUCCESS)
        return redirect("admin:employees_employeeprocessauthorization_processes")

    def authorization_delete_view(self, request):
        self._require_superuser(request)
        if request.method != "POST":
            return redirect("admin:employees_employeeprocessauthorization_changelist")
        authorization_ids = request.POST.getlist("authorization_ids")
        authorizations = EmployeeProcessAuthorization.objects.filter(pk__in=authorization_ids)
        if not authorizations.exists():
            self.message_user(request, "请先选择需要删除的员工工艺授权。", messages.WARNING)
            return redirect("admin:employees_employeeprocessauthorization_changelist")

        with transaction.atomic():
            details = list(authorizations.values("id", "employee_id", "process_id"))
            employee_ids = [item["employee_id"] for item in details]
            authorizations.delete()
            AuditLog.objects.create(
                actor=request.user,
                action="employee_process_authorization.delete",
                target_type="EmployeeProcessAuthorization",
                target_id=",".join(str(item["id"]) for item in details),
                metadata={"authorizations": details},
            )
            self._enqueue_employee_syncs(employee_ids)
        self.message_user(request, f"已删除 {len(details)} 条员工工艺授权。", messages.SUCCESS)
        return redirect("admin:employees_employeeprocessauthorization_changelist")

    def process_add_view(self, request):
        self._require_superuser(request)
        form = WorkProcessManagementForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            process = form.save(commit=False)
            process.code = f"process-{uuid4().hex}"
            process.save()
            AuditLog.objects.create(
                actor=request.user,
                action="work_process.create",
                target_type="WorkProcess",
                target_id=str(process.pk),
                metadata={"code": process.code, "name": process.name, "position": process.position.code},
            )
            self.message_user(request, "具体工艺已新增。", messages.SUCCESS)
            return redirect("admin:employees_employeeprocessauthorization_processes")
        return TemplateResponse(
            request,
            "admin/employees/employeeprocessauthorization/process_form.html",
            self._process_management_context(request, title="员工工艺授权 - 新增工艺", form=form, process=None),
        )

    def process_change_view(self, request, process_id):
        self._require_superuser(request)
        process = get_object_or_404(WorkProcess, pk=process_id)
        form = WorkProcessManagementForm(request.POST or None, instance=process)
        sop_form = ProcessSOPUploadForm(request.POST or None, request.FILES or None)
        if request.method == "POST" and request.POST.get("_upload_sop"):
            if sop_form.is_valid():
                sop = sop_form.save(commit=False)
                sop.process = process
                sop.uploaded_by = request.user
                ProcessSOP.objects.filter(process=process, title=sop.title, is_active=True).update(is_active=False)
                sop.save()
                AuditLog.objects.create(actor=request.user, action="process_sop.upload", target_type="ProcessSOP", target_id=str(sop.pk), metadata={"process": process.code, "title": sop.title, "version": sop.version})
                self.message_user(request, "SOP 已上传并启用。", messages.SUCCESS)
                return redirect("admin:employees_employeeprocessauthorization_process_change", process_id=process.pk)
        elif request.method == "POST" and form.is_valid():
            process = form.save()
            AuditLog.objects.create(
                actor=request.user,
                action="work_process.update",
                target_type="WorkProcess",
                target_id=str(process.pk),
                metadata={"code": process.code, "name": process.name, "position": process.position.code, "is_active": process.is_active},
            )
            self._sync_process_status_to_authorizations(process, request.user)
            self.message_user(request, "具体工艺已更新。", messages.SUCCESS)
            return redirect("admin:employees_employeeprocessauthorization_processes")
        return TemplateResponse(
            request,
            "admin/employees/employeeprocessauthorization/process_form.html",
            self._process_management_context(request, title="员工工艺授权 - 编辑工艺", form=form, process=process, sop_form=sop_form),
        )

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
        process = WorkProcess.objects.get(pk=obj.process_id)
        if process.is_active != obj.is_active:
            process.is_active = obj.is_active
            process.save(update_fields=("is_active", "updated_at"))
        self._sync_process_status_to_authorizations(process, request.user)
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
