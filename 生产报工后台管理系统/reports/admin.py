from django.contrib import admin
from django.utils import timezone

from .models import AuditLog, ReportMaterialSnapshot, ReportSyncEvent, WorkReport

admin.site.site_header = "生产报工管理"
admin.site.site_title = "生产报工管理系统"
admin.site.index_title = "管理工作台"
admin.site.enable_nav_sidebar = False


class ReportMaterialSnapshotInline(admin.TabularInline):
    model = ReportMaterialSnapshot
    extra = 0
    can_delete = False
    fields = ("actual_quantity", "created_at")
    readonly_fields = fields


class ReportSyncEventInline(admin.TabularInline):
    model = ReportSyncEvent
    extra = 0
    can_delete = False
    fields = ("step", "status", "message", "occurred_at", "created_at")
    readonly_fields = fields


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor", "action", "target_type")
    list_filter = ("action", "target_type", "created_at")
    search_fields = ("action", "target_type", "target_id", "actor__username")
    ordering = ("-created_at", "-id")
    readonly_fields = ("actor", "action", "target_type", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(WorkReport)
class WorkReportAdmin(admin.ModelAdmin):
    list_display = ("production_order", "reported_at", "worker_name", "operation_name", "quantity", "sync_status", "review_status")
    list_filter = ("reported_at", "worker_name", "sync_status", "review_status")
    search_fields = ("production_name", "worker_name", "order_customer", "order_product", "operation_name")
    ordering = ("-reported_at",)
    date_hierarchy = "reported_at"
    list_per_page = 50
    actions = ("approve_reports", "void_reports")
    fields = (
        "production_name",
        "worker_name", "worker_team", "operation_name",
        "order_customer", "order_product", "quantity", "qualified_quantity", "reported_at",
        "hours", "remark", "sync_status", "material_sync_status",
        "odoo_progress_quantity", "error_message", "review_status", "reviewed_by", "reviewed_at", "created_at", "updated_at",
    )
    readonly_fields = fields
    inlines = (ReportMaterialSnapshotInline, ReportSyncEventInline)

    def render_change_form(self, request, context, add=False, change=False, form_url="", obj=None):
        if change:
            context["title"] = self.opts.verbose_name
        return super().render_change_form(request, context, add, change, form_url, obj)

    def has_delete_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not request.user.has_perm("reports.review_workreport"):
            actions.pop("approve_reports", None)
            actions.pop("void_reports", None)
        return actions

    @admin.display(description="生产单", ordering="production_name")
    def production_order(self, obj):
        production = obj.production_name or obj.production_id
        return f"{production} {obj.order_product}" if obj.order_product else production

    @admin.action(description="审核选中的报工")
    def approve_reports(self, request, queryset):
        if not request.user.has_perm("reports.review_workreport"):
            self.message_user(request, "当前账号没有报工审核权限", level="error")
            return
        updated = queryset.exclude(review_status=WorkReport.ReviewStatus.VOIDED).update(
            review_status=WorkReport.ReviewStatus.APPROVED, reviewed_by=request.user, reviewed_at=timezone.now()
        )
        AuditLog.objects.create(actor=request.user, action="approve", target_type="work_report_batch", metadata={"count": updated})
        self.message_user(request, f"已审核 {updated} 条报工。")

    @admin.action(description="作废选中的报工")
    def void_reports(self, request, queryset):
        if not request.user.has_perm("reports.review_workreport"):
            self.message_user(request, "当前账号没有报工作废权限", level="error")
            return
        updated = queryset.exclude(review_status=WorkReport.ReviewStatus.VOIDED).update(
            review_status=WorkReport.ReviewStatus.VOIDED, sync_status=WorkReport.SyncStatus.CANCELLED,
            reviewed_by=request.user, reviewed_at=timezone.now()
        )
        AuditLog.objects.create(actor=request.user, action="void", target_type="work_report_batch", metadata={"count": updated})
        self.message_user(request, f"已作废 {updated} 条报工。")
