from django.contrib import admin
from django.utils import timezone

from .models import AuditLog, ReportMaterialSnapshot, ReportSyncEvent, WorkReport

admin.site.site_header = "生产报工管理"
admin.site.site_title = "生产报工管理系统"
admin.site.index_title = "管理工作台"
admin.site.enable_nav_sidebar = False


class MaterialSnapshotInline(admin.TabularInline):
    model = ReportMaterialSnapshot
    extra = 0
    can_delete = False
    readonly_fields = ("product_id", "bom_line_id", "default_code", "actual_quantity", "uom_id", "created_at")


class SyncEventInline(admin.TabularInline):
    model = ReportSyncEvent
    extra = 0
    can_delete = False
    readonly_fields = ("step", "status", "message", "payload", "occurred_at", "created_at")


@admin.register(WorkReport)
class WorkReportAdmin(admin.ModelAdmin):
    list_display = ("production_order", "reported_at", "worker_name", "operation_name", "production_id", "workorder_id", "quantity", "sync_status", "review_status")
    list_filter = ("sync_status", "review_status", "operation_code", "reported_at")
    search_fields = ("source_report_id", "production_name", "worker_id", "worker_name", "production_id", "workorder_id", "order_id", "operation_name")
    ordering = ("-reported_at",)
    date_hierarchy = "reported_at"
    list_per_page = 50
    actions = ("approve_reports", "void_reports")
    inlines = (MaterialSnapshotInline, SyncEventInline)
    readonly_fields = (
        "source_report_id", "idempotency_key", "production_id", "production_name", "workorder_id", "worker_id", "worker_name", "worker_team",
        "operation_code", "operation_name", "order_id", "order_customer", "order_product", "quantity", "qualified_quantity",
        "hours", "remark", "reported_at", "sync_status", "material_sync_status", "odoo_report_id", "odoo_stock_move_ids",
        "odoo_progress_quantity", "error_message", "review_status", "reviewed_by", "reviewed_at", "created_at", "updated_at",
    )

    def has_delete_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False

    @admin.display(description="生产单", ordering="production_name")
    def production_order(self, obj):
        production = obj.production_name or obj.production_id
        return f"{production} {obj.order_product}" if obj.order_product else production

    @admin.action(description="审核选中的报工")
    def approve_reports(self, request, queryset):
        updated = queryset.exclude(review_status=WorkReport.ReviewStatus.VOIDED).update(
            review_status=WorkReport.ReviewStatus.APPROVED, reviewed_by=request.user, reviewed_at=timezone.now()
        )
        AuditLog.objects.create(actor=request.user, action="approve", target_type="work_report_batch", metadata={"count": updated})
        self.message_user(request, f"已审核 {updated} 条报工。")

    @admin.action(description="作废选中的报工")
    def void_reports(self, request, queryset):
        updated = queryset.exclude(review_status=WorkReport.ReviewStatus.VOIDED).update(
            review_status=WorkReport.ReviewStatus.VOIDED, sync_status=WorkReport.SyncStatus.CANCELLED,
            reviewed_by=request.user, reviewed_at=timezone.now()
        )
        AuditLog.objects.create(actor=request.user, action="void", target_type="work_report_batch", metadata={"count": updated})
        self.message_user(request, f"已作废 {updated} 条报工。")
