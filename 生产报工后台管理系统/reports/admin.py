from django.contrib import admin
from django.utils import timezone

from .models import AuditLog, WorkReport

admin.site.site_header = "生产报工管理"
admin.site.site_title = "生产报工管理系统"
admin.site.index_title = "管理工作台"
admin.site.enable_nav_sidebar = False


@admin.register(WorkReport)
class WorkReportAdmin(admin.ModelAdmin):
    list_display = ("production_order", "reported_at", "worker_name", "operation_name", "production_id", "workorder_id", "quantity", "sync_status", "review_status")
    list_filter = ("sync_status", "review_status", "operation_code", "reported_at")
    search_fields = ("source_report_id", "production_name", "worker_id", "worker_name", "production_id", "workorder_id", "order_id", "operation_name")
    ordering = ("-reported_at",)
    date_hierarchy = "reported_at"
    list_per_page = 50
    actions = ("approve_reports", "void_reports")
    fields = (
        "production_name", "production_id", "workorder_id",
        "worker_name", "worker_team", "operation_name",
        "order_customer", "order_product", "quantity", "qualified_quantity", "reported_at",
        "sync_status", "odoo_report_id", "review_status",
    )
    readonly_fields = fields

    def render_change_form(self, request, context, add=False, change=False, form_url="", obj=None):
        if change:
            context["title"] = self.opts.verbose_name
        return super().render_change_form(request, context, add, change, form_url, obj)

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
