import csv
import hmac
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.auth.decorators import login_required, permission_required
from django.db import IntegrityError, transaction
from django.db.models import Count, Sum
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import AuditLog, ReportMaterialSnapshot, ReportSyncEvent, WorkReport


SYNC_STATUS_MAP = {
    "local": WorkReport.SyncStatus.PENDING,
    "odoo_pending": WorkReport.SyncStatus.PENDING,
    "odoo_synced": WorkReport.SyncStatus.SYNCED,
    "odoo_partial": WorkReport.SyncStatus.PARTIAL,
    "odoo_failed": WorkReport.SyncStatus.FAILED,
}


def _audit(user, action, target_type="", target_id="", **metadata):
    AuditLog.objects.create(actor=user if getattr(user, "is_authenticated", False) else None, action=action, target_type=target_type, target_id=str(target_id), metadata=metadata)


def _api_authorized(request):
    supplied = request.headers.get("X-Internal-API-Key", "")
    return supplied and hmac.compare_digest(supplied, settings.INTERNAL_REPORT_API_KEY)


def _value(data, camel, snake=None, default=""):
    return data.get(camel, data.get(snake or camel, default))


def _parse_reported_at(data):
    value = _value(data, "reportedAt", "reported_at")
    if value:
        parsed = parse_datetime(value)
        if parsed:
            return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed
    date = str(data.get("date", ""))
    time = str(data.get("time", "00:00"))
    try:
        return timezone.make_aware(datetime.fromisoformat(f"{date}T{time}"))
    except ValueError as exc:
        raise ValueError("reportedAt 或 date/time 必须是有效时间") from exc


def _get_report_from_identity(data):
    source_report_id = str(_value(data, "sourceReportId", "source_report_id"))
    idempotency_key = str(_value(data, "idempotencyKey", "idempotency_key"))
    if not source_report_id or not idempotency_key:
        return None, JsonResponse({"detail": "sourceReportId and idempotencyKey are required"}, status=400)
    report = WorkReport.objects.filter(source_report_id=source_report_id, idempotency_key=idempotency_key).first()
    if report:
        return report, None
    if WorkReport.objects.filter(source_report_id=source_report_id).exists() or WorkReport.objects.filter(idempotency_key=idempotency_key).exists():
        return None, JsonResponse({"detail": "Report identity conflict"}, status=409)
    return None, JsonResponse({"detail": "Work report not found"}, status=404)


@csrf_exempt
@require_POST
def receive_work_report(request):
    if not _api_authorized(request):
        return JsonResponse({"detail": "Unauthorized"}, status=401)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"detail": "Invalid JSON"}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({"detail": "JSON body must be an object"}, status=400)

    required = {"sourceReportId": _value(data, "sourceReportId", "source_report_id"), "idempotencyKey": _value(data, "idempotencyKey", "idempotency_key"), "productionId": _value(data, "productionId", "production_id"), "workorderId": _value(data, "workorderId", "workorder_id"), "workerId": _value(data, "workerId", "worker_id"), "workerName": _value(data, "workerName", "worker_name"), "operation": data.get("operation"), "operationLabel": _value(data, "operationLabel", "operation_label"), "qty": data.get("qty")}
    missing = [name for name, value in required.items() if value in (None, "")]
    if missing:
        return JsonResponse({"detail": "Missing fields", "fields": missing}, status=400)
    try:
        quantity = int(data["qty"])
        qualified = int(data.get("qualified", 0))
        hours = Decimal(str(data.get("hours", 0)))
        if quantity <= 0 or qualified < 0 or hours < 0:
            raise ValueError
        reported_at = _parse_reported_at(data)
    except (ValueError, TypeError, InvalidOperation):
        return JsonResponse({"detail": "qty must be positive; qualified and hours cannot be negative"}, status=400)

    raw_sync_status = str(_value(data, "syncStatus", "sync_status", "pending"))
    sync_status = SYNC_STATUS_MAP.get(raw_sync_status, raw_sync_status)
    if sync_status not in WorkReport.SyncStatus.values:
        sync_status = WorkReport.SyncStatus.PENDING
    materials = data.get("materials", [])
    events = data.get("syncEvents", data.get("sync_events", []))
    if not isinstance(materials, list) or not isinstance(events, list):
        return JsonResponse({"detail": "materials and syncEvents must be arrays"}, status=400)

    defaults = {
        "production_id": str(required["productionId"]), "production_name": str(_value(data, "productionName", "production_name", "")),
        "workorder_id": str(required["workorderId"]),
        "worker_id": str(required["workerId"]), "worker_name": str(required["workerName"]),
        "worker_team": str(_value(data, "workerTeam", "worker_team", "")), "operation_code": str(required["operation"]),
        "operation_name": str(required["operationLabel"]), "order_id": str(_value(data, "orderId", "order_id", "")),
        "order_customer": str(_value(data, "orderCustomer", "order_customer", "")), "order_product": str(_value(data, "orderProduct", "order_product", "")),
        "quantity": quantity, "qualified_quantity": qualified, "hours": hours, "remark": str(data.get("remark", "")),
        "reported_at": reported_at, "sync_status": sync_status, "material_sync_status": str(_value(data, "materialSyncStatus", "material_sync_status", "")),
        "odoo_report_id": str(_value(data, "odooReportId", "odoo_report_id", "")), "odoo_stock_move_ids": _value(data, "odooStockMoveIds", "odoo_stock_move_ids", []),
        "odoo_progress_quantity": _value(data, "odooProgressQty", "odoo_progress_qty", None), "error_message": str(_value(data, "errorMessage", "error_message", "")),
    }
    if isinstance(defaults["odoo_stock_move_ids"], str):
        try: defaults["odoo_stock_move_ids"] = json.loads(defaults["odoo_stock_move_ids"])
        except json.JSONDecodeError: defaults["odoo_stock_move_ids"] = []
    source_report_id = str(required["sourceReportId"])
    idempotency_key = str(required["idempotencyKey"])
    existing_by_key = WorkReport.objects.filter(idempotency_key=idempotency_key).first()
    if existing_by_key:
        if existing_by_key.source_report_id != source_report_id:
            return JsonResponse({"detail": "idempotencyKey conflicts with a different sourceReportId"}, status=409)
        return JsonResponse({"id": existing_by_key.pk, "created": False, "sourceReportId": existing_by_key.source_report_id})
    existing_by_source = WorkReport.objects.filter(source_report_id=source_report_id).first()
    if existing_by_source:
        if existing_by_source.idempotency_key != idempotency_key:
            return JsonResponse({"detail": "sourceReportId conflicts with a different idempotencyKey"}, status=409)
        return JsonResponse({"id": existing_by_source.pk, "created": False, "sourceReportId": existing_by_source.source_report_id})
    try:
        with transaction.atomic():
            report = WorkReport.objects.create(source_report_id=source_report_id, idempotency_key=idempotency_key, **defaults)
            ReportMaterialSnapshot.objects.bulk_create([ReportMaterialSnapshot(work_report=report, product_id=str(item.get("productId", "")), bom_line_id=str(item.get("bomLineId", "")), default_code=str(item.get("defaultCode", "")), actual_quantity=Decimal(str(item.get("actualQty", 0))), uom_id=str(item.get("uomId", ""))) for item in materials])
            ReportSyncEvent.objects.bulk_create([ReportSyncEvent(work_report=report, step=str(item.get("step", "report")), status=str(item.get("status", sync_status)), message=str(item.get("message", "")), payload=item.get("payload", {}), occurred_at=parse_datetime(item.get("occurredAt", "")) or timezone.now()) for item in events])
            if not events:
                ReportSyncEvent.objects.create(work_report=report, step=ReportSyncEvent.Step.REPORT, status=sync_status, message=defaults["error_message"], occurred_at=timezone.now())
    except (IntegrityError, InvalidOperation, ValueError):
        existing = WorkReport.objects.filter(idempotency_key=idempotency_key, source_report_id=source_report_id).first()
        if existing:
            return JsonResponse({"id": existing.pk, "created": False, "sourceReportId": existing.source_report_id}, status=200)
        return JsonResponse({"detail": "Invalid work report payload"}, status=400)
    return JsonResponse({"id": report.pk, "created": True, "sourceReportId": report.source_report_id}, status=201)


@csrf_exempt
@require_POST
def receive_sync_status(request):
    if not _api_authorized(request):
        return JsonResponse({"detail": "Unauthorized"}, status=401)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"detail": "Invalid JSON"}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({"detail": "JSON body must be an object"}, status=400)
    report, error = _get_report_from_identity(data)
    if error:
        return error
    raw_sync_status = str(_value(data, "syncStatus", "sync_status"))
    sync_status = SYNC_STATUS_MAP.get(raw_sync_status, raw_sync_status)
    if sync_status not in WorkReport.SyncStatus.values:
        return JsonResponse({"detail": "Invalid syncStatus"}, status=400)
    event_key = str(_value(data, "eventKey", "event_key"))
    if not event_key:
        return JsonResponse({"detail": "eventKey is required"}, status=400)
    if len(event_key) > 200:
        return JsonResponse({"detail": "eventKey is too long"}, status=400)
    values = {
        "sync_status": sync_status,
        "material_sync_status": str(_value(data, "materialSyncStatus", "material_sync_status", report.material_sync_status)),
        "odoo_report_id": str(_value(data, "odooReportId", "odoo_report_id", report.odoo_report_id)),
        "odoo_stock_move_ids": _value(data, "odooStockMoveIds", "odoo_stock_move_ids", report.odoo_stock_move_ids),
        "odoo_progress_quantity": _value(data, "odooProgressQty", "odoo_progress_qty", report.odoo_progress_quantity),
        "error_message": str(_value(data, "errorMessage", "error_message", report.error_message)),
    }
    if isinstance(values["odoo_stock_move_ids"], str):
        try:
            values["odoo_stock_move_ids"] = json.loads(values["odoo_stock_move_ids"])
        except json.JSONDecodeError:
            return JsonResponse({"detail": "odooStockMoveIds must be JSON"}, status=400)
    try:
        with transaction.atomic():
            event, created = ReportSyncEvent.objects.get_or_create(
                event_key=event_key,
                defaults={
                    "work_report": report,
                    "step": ReportSyncEvent.Step.PROGRESS,
                    "status": sync_status,
                    "message": values["error_message"],
                    "payload": data,
                    "occurred_at": timezone.now(),
                },
            )
            if not created and event.work_report_id != report.id:
                return JsonResponse({"detail": "eventKey conflicts with another work report"}, status=409)
            if created:
                WorkReport.objects.filter(pk=report.pk).update(**values)
    except (IntegrityError, InvalidOperation, ValueError):
        return JsonResponse({"detail": "Invalid sync status payload"}, status=400)
    return JsonResponse({"id": report.pk, "created": created, "eventKey": event_key})


@login_required
@permission_required("reports.view_workreport", raise_exception=True)
@require_GET
def statistics(request):
    today = timezone.localdate()
    reports = WorkReport.objects.filter(reported_at__date=today)
    payload = {"date": str(today), "summary": reports.aggregate(report_count=Count("id"), quantity=Sum("quantity")), "by_operation": list(reports.values("operation_code", "operation_name").annotate(report_count=Count("id"), quantity=Sum("quantity")).order_by("operation_name")), "by_worker": list(reports.values("worker_id", "worker_name").annotate(report_count=Count("id"), quantity=Sum("quantity")).order_by("worker_name"))}
    _audit(request.user, "view_statistics", date=str(today))
    return JsonResponse(payload)


@login_required
@permission_required("reports.export_workreport", raise_exception=True)
@require_GET
def export_csv(request):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="work-reports.csv"'
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(["原系统报工ID", "报工时间", "员工", "工序", "MO", "WO", "数量", "同步状态", "审核状态", "错误信息"])
    for report in WorkReport.objects.all().iterator():
        writer.writerow([report.source_report_id, report.reported_at.isoformat(), report.worker_name, report.operation_name, report.production_id, report.workorder_id, report.quantity, report.sync_status, report.review_status, report.error_message])
    _audit(request.user, "export", target_type="work_report")
    return response
