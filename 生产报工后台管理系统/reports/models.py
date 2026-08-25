from django.conf import settings
from django.db import models


class WorkReport(models.Model):
    class SyncStatus(models.TextChoices):
        PENDING = "pending", "待同步"
        SYNCED = "synced", "已同步"
        PARTIAL = "partial", "部分成功"
        FAILED = "failed", "同步失败"
        CANCELLED = "cancelled", "已作废"

    class ReviewStatus(models.TextChoices):
        PENDING = "pending", "待审核"
        APPROVED = "approved", "已审核"
        VOIDED = "voided", "已作废"
        CORRECTED = "corrected", "已更正"

    source_report_id = models.CharField("原系统报工 ID", max_length=64, unique=True)
    idempotency_key = models.CharField("幂等键", max_length=128, unique=True)
    production_id = models.CharField("MO ID", max_length=64)
    production_name = models.CharField("生产单", max_length=128, blank=True)
    workorder_id = models.CharField("WO ID", max_length=64)
    worker_id = models.CharField("员工 ID", max_length=64)
    worker_name = models.CharField("员工姓名", max_length=128)
    worker_team = models.CharField("班组", max_length=128, blank=True)
    operation_code = models.CharField("工序代码", max_length=128)
    operation_name = models.CharField("工序名称", max_length=128)
    job_role_code = models.CharField("岗位代码", max_length=64, blank=True)
    job_role_name = models.CharField("岗位名称", max_length=128, blank=True)
    process_code = models.CharField("具体工艺代码", max_length=128, blank=True)
    process_name = models.CharField("具体工艺名称", max_length=128, blank=True)
    order_id = models.CharField("订单号", max_length=128, blank=True)
    order_customer = models.CharField("客户", max_length=255, blank=True)
    order_product = models.CharField("产品", max_length=255, blank=True)
    quantity = models.PositiveIntegerField("报工数量")
    qualified_quantity = models.PositiveIntegerField("合格数量", default=0)
    hours = models.DecimalField("工时", max_digits=10, decimal_places=2, default=0)
    remark = models.TextField("备注", blank=True)
    reported_at = models.DateTimeField("报工时间")
    sync_status = models.CharField("同步状态", max_length=16, choices=SyncStatus.choices, default=SyncStatus.PENDING)
    material_sync_status = models.CharField("物料同步状态", max_length=32, blank=True)
    odoo_report_id = models.CharField("Odoo 报工 ID", max_length=64, blank=True)
    odoo_stock_move_ids = models.JSONField("Odoo 库存移动 ID", default=list, blank=True)
    odoo_progress_quantity = models.DecimalField("Odoo 工单进度", max_digits=12, decimal_places=2, null=True, blank=True)
    error_message = models.TextField("错误信息", blank=True)
    review_status = models.CharField("审核状态", max_length=16, choices=ReviewStatus.choices, default=ReviewStatus.PENDING)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="reviewed_work_reports")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "报工记录"
        verbose_name_plural = "报工记录"
        ordering = ["-reported_at", "-id"]
        indexes = [
            models.Index(fields=["reported_at"], name="report_reported_at_idx"),
            models.Index(fields=["worker_id", "reported_at"], name="report_worker_date_idx"),
            models.Index(fields=["workorder_id", "operation_code"], name="report_wo_operation_idx"),
            models.Index(fields=["sync_status"], name="report_sync_status_idx"),
        ]
        permissions = [("export_workreport", "Can export work reports"), ("review_workreport", "Can review work reports")]

    def __str__(self):
        return f"{self.source_report_id} | {self.worker_name} | {self.operation_name}"


class ReportMaterialSnapshot(models.Model):
    work_report = models.ForeignKey(WorkReport, on_delete=models.PROTECT, related_name="material_snapshots")
    product_id = models.CharField(max_length=64)
    bom_line_id = models.CharField(max_length=64, blank=True)
    default_code = models.CharField(max_length=128, blank=True)
    actual_quantity = models.DecimalField(max_digits=12, decimal_places=3)
    uom_id = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "物料确认快照"
        verbose_name_plural = "物料确认快照"


class ReportSyncEvent(models.Model):
    class Step(models.TextChoices):
        REPORT = "report", "报工记录"
        MATERIAL = "material", "物料同步"
        PROGRESS = "progress", "WO/MO 进度"

    work_report = models.ForeignKey(WorkReport, on_delete=models.PROTECT, related_name="sync_events")
    event_key = models.CharField(max_length=200, unique=True, null=True, blank=True)
    step = models.CharField(max_length=32, choices=Step.choices)
    status = models.CharField(max_length=32)
    message = models.TextField(blank=True)
    payload = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at", "-id"]
        verbose_name = "同步事件"
        verbose_name_plural = "同步事件"


class AuditLog(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=64)
    target_type = models.CharField(max_length=64, blank=True)
    target_id = models.CharField(max_length=64, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "管理操作审计"
        verbose_name_plural = "管理操作审计"
