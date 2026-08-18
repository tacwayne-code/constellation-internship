import json
import hashlib
import logging
import re
import threading
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import IntegrityError, close_old_connections, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import ReportSyncEvent, WorkReport


logger = logging.getLogger(__name__)

PRODUCTION_DETAILS_CACHE_TTL = 30
_production_details_cache = {"data": {}, "updated_at": 0.0}
_production_details_cache_lock = threading.Lock()
_production_details_refresh_lock = threading.Lock()

SYNC_STATUS_MAP = {
    "local": WorkReport.SyncStatus.PENDING,
    "odoo_pending": WorkReport.SyncStatus.PENDING,
    "odoo_synced": WorkReport.SyncStatus.SYNCED,
    "odoo_partial": WorkReport.SyncStatus.PARTIAL,
    "odoo_failed": WorkReport.SyncStatus.FAILED,
    "pending": WorkReport.SyncStatus.PENDING,
    "synced": WorkReport.SyncStatus.SYNCED,
    "partial": WorkReport.SyncStatus.PARTIAL,
    "failed": WorkReport.SyncStatus.FAILED,
    "cancelled": WorkReport.SyncStatus.CANCELLED,
}


class SopSyncError(RuntimeError):
    pass


def _value(data, camel, snake=None, default=""):
    return data.get(camel, data.get(snake or camel, default))


def _reported_at(data):
    value = _value(data, "reportedAt", "reported_at")
    if value:
        parsed = parse_datetime(str(value))
        if parsed:
            return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed
    try:
        parsed = datetime.fromisoformat(f"{data['date']}T{data.get('time', '00:00')}")
    except (KeyError, TypeError, ValueError) as exc:
        raise SopSyncError("SOP report has an invalid date/time") from exc
    return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed


def _fetch_sop_data(url, label):
    request = Request(
        url,
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=10) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise SopSyncError(f"Unable to read SOP {label}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise SopSyncError(f"SOP {label} API returned an unsuccessful response")
    data = payload.get("data", [])
    if not isinstance(data, list):
        raise SopSyncError(f"SOP {label} API data must be an array")
    return data


def fetch_sop_reports():
    return _fetch_sop_data(settings.SOP_REPORTS_API_URL, "reports")


def _normalize_product_name(value):
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        decoded = text.encode("latin-1").decode("gbk")
    except (UnicodeEncodeError, UnicodeDecodeError):
        decoded = text
    if any("\u4e00" <= char <= "\u9fff" for char in decoded):
        text = decoded
    return re.sub(r"^\[[^]]+\]\s*", "", text).strip()


def _production_details_cache_snapshot():
    with _production_details_cache_lock:
        return dict(_production_details_cache["data"]), _production_details_cache["updated_at"]


def get_cached_production_details():
    return _production_details_cache_snapshot()[0]


def fetch_production_details():
    cached_details, cached_at = _production_details_cache_snapshot()
    now = time.monotonic()
    if cached_details and now - cached_at < PRODUCTION_DETAILS_CACHE_TTL:
        return cached_details

    with _production_details_refresh_lock:
        now = time.monotonic()
        cached_details, cached_at = _production_details_cache_snapshot()
        if cached_details and now - cached_at < PRODUCTION_DETAILS_CACHE_TTL:
            return cached_details

        records = []
        errors = []
        for url, label in (
            (settings.SOP_ORDER_SUMMARY_API_URL, "order summary"),
            (settings.SOP_WORKORDERS_API_URL, "workorders"),
        ):
            try:
                records.extend(_fetch_sop_data(url, label))
            except SopSyncError as exc:
                errors.append(str(exc))
        details = {
            str(item.get("productionId")): {
                "production_name": str(item.get("productionName", "")).strip(),
                "product_name": _normalize_product_name(item.get("productName", "")),
            }
            for item in records
            if isinstance(item, dict) and item.get("productionId") not in (None, "") and item.get("productionName")
        }
        if not details:
            if cached_details:
                if errors:
                    logger.warning("SOP production details refresh failed; continuing with stale cache: %s", "; ".join(errors))
                with _production_details_cache_lock:
                    _production_details_cache["updated_at"] = now
                return dict(cached_details)
            if errors:
                raise SopSyncError("; ".join(errors))
            return {}
        with _production_details_cache_lock:
            _production_details_cache["data"] = details
            _production_details_cache["updated_at"] = now
        return dict(details)


def _report_values(data, production_details=None):
    required = {
        "sourceReportId": _value(data, "id", "source_report_id"),
        "idempotencyKey": _value(data, "idempotencyKey", "idempotency_key"),
        "productionId": _value(data, "productionId", "production_id"),
        "workorderId": _value(data, "workorderId", "workorder_id"),
        "workerId": _value(data, "workerId", "worker_id"),
        "workerName": _value(data, "workerName", "worker_name"),
        "operation": data.get("operation"),
        "operationLabel": _value(data, "operationLabel", "operation_label"),
        "qty": data.get("qty"),
    }
    missing = [name for name, value in required.items() if value in (None, "")]
    if missing:
        raise SopSyncError(f"SOP report is missing fields: {', '.join(missing)}")
    try:
        quantity = int(required["qty"])
        qualified = int(data.get("qualified", quantity))
        hours = Decimal(str(data.get("hours", 0)))
        progress = _value(data, "odooProgressQty", "odoo_progress_qty", None)
        progress = Decimal(str(progress)) if progress not in (None, "") else None
    except (TypeError, ValueError, InvalidOperation) as exc:
        raise SopSyncError("SOP report has invalid numeric values") from exc
    if quantity <= 0 or qualified < 0 or hours < 0:
        raise SopSyncError("SOP report quantities or hours are invalid")
    stock_move_ids = _value(data, "odooStockMoveIds", "odoo_stock_move_ids", [])
    if isinstance(stock_move_ids, str):
        try:
            stock_move_ids = json.loads(stock_move_ids or "[]")
        except json.JSONDecodeError:
            stock_move_ids = []
    raw_status = str(_value(data, "syncStatus", "sync_status", "local"))
    production_id = str(required["productionId"])
    detail = (production_details or {}).get(production_id, {})
    production_name = str(_value(data, "productionName", "production_name", "")).strip()
    if not production_name:
        production_name = detail.get("production_name", "")
    product_name = _normalize_product_name(_value(data, "orderProduct", "order_product", ""))
    if not product_name:
        product_name = detail.get("product_name", "")
    return required, {
        "production_id": production_id,
        "production_name": production_name,
        "workorder_id": str(required["workorderId"]),
        "worker_id": str(required["workerId"]),
        "worker_name": str(required["workerName"]),
        "worker_team": str(_value(data, "workerTeam", "worker_team", "")),
        "operation_code": str(required["operation"]),
        "operation_name": str(required["operationLabel"]),
        "order_id": str(_value(data, "orderId", "order_id", "")),
        "order_customer": str(_value(data, "orderCustomer", "order_customer", "")),
        "order_product": product_name,
        "quantity": quantity,
        "qualified_quantity": qualified,
        "hours": hours,
        "remark": str(data.get("remark", "")),
        "reported_at": _reported_at(data),
        "sync_status": SYNC_STATUS_MAP.get(raw_status, WorkReport.SyncStatus.PENDING),
        "material_sync_status": str(_value(data, "materialSyncStatus", "material_sync_status", "")),
        "odoo_report_id": str(_value(data, "odooReportId", "odoo_report_id", "")),
        "odoo_stock_move_ids": stock_move_ids,
        "odoo_progress_quantity": progress,
        "error_message": str(_value(data, "errorMessage", "error_message", "")),
    }


def sync_report(data, production_details=None):
    if not isinstance(data, dict) or data.get("odooDisplayOnly"):
        return "skipped"
    required, values = _report_values(data, production_details)
    source_id = str(required["sourceReportId"])
    idempotency_key = str(required["idempotencyKey"])
    with transaction.atomic():
        report = WorkReport.objects.select_for_update().filter(source_report_id=source_id).first()
        if report and report.idempotency_key != idempotency_key:
            raise SopSyncError(f"Identity conflict for SOP report {source_id}")
        keyed_report = WorkReport.objects.select_for_update().filter(idempotency_key=idempotency_key).first()
        if keyed_report and keyed_report.source_report_id != source_id:
            raise SopSyncError(f"Idempotency conflict for SOP report {source_id}")
        if report is None:
            report = WorkReport.objects.create(
                source_report_id=source_id,
                idempotency_key=idempotency_key,
                **values,
            )
            ReportSyncEvent.objects.create(
                work_report=report,
                event_key=f"sop-pull:{source_id}:created",
                step=ReportSyncEvent.Step.REPORT,
                status=values["sync_status"],
                message=values["error_message"],
                payload={"source": "sop_http_pull"},
                occurred_at=timezone.now(),
            )
            return "created"
        previous_sync_values = {
            field: getattr(report, field)
            for field in (
                "sync_status", "material_sync_status", "odoo_report_id",
                "odoo_stock_move_ids", "odoo_progress_quantity", "error_message",
            )
        }
        if not values["production_name"]:
            values["production_name"] = report.production_name
        if not values["order_product"]:
            values["order_product"] = report.order_product
        changed = any(getattr(report, field) != value for field, value in values.items())
        if changed:
            for field, value in values.items():
                setattr(report, field, value)
            report.save(update_fields=(*values.keys(), "updated_at"))
            current_sync_values = {field: values[field] for field in previous_sync_values}
            if current_sync_values != previous_sync_values:
                fingerprint = hashlib.sha256(
                    json.dumps(current_sync_values, ensure_ascii=False, default=str, sort_keys=True).encode("utf-8")
                ).hexdigest()[:24]
                ReportSyncEvent.objects.get_or_create(
                    event_key=f"sop-pull:{source_id}:state:{fingerprint}",
                    defaults={
                        "work_report": report,
                        "step": ReportSyncEvent.Step.PROGRESS,
                        "status": values["sync_status"],
                        "message": values["error_message"],
                        "payload": {"source": "sop_http_pull", "syncStatus": values["sync_status"]},
                        "occurred_at": timezone.now(),
                    },
                )
            return "updated"
    return "unchanged"


def sync_sop_reports():
    summary = {"fetched": 0, "created": 0, "updated": 0, "unchanged": 0, "skipped": 0, "errors": 0}
    reports = fetch_sop_reports()
    try:
        production_details = fetch_production_details()
    except SopSyncError as exc:
        logger.warning("SOP production names are temporarily unavailable: %s", exc)
        production_details = {}
    summary["fetched"] = len(reports)
    for data in reports:
        try:
            result = sync_report(data, production_details)
            summary[result] += 1
        except (SopSyncError, IntegrityError) as exc:
            summary["errors"] += 1
            logger.warning("SOP report sync skipped one record: %s", exc)
    return summary


def run_sync_loop():
    while True:
        close_old_connections()
        try:
            summary = sync_sop_reports()
            if summary["created"] or summary["updated"] or summary["errors"]:
                logger.info("SOP report sync: %s", summary)
        except SopSyncError as exc:
            logger.warning("SOP report sync failed: %s", exc)
        finally:
            close_old_connections()
        time.sleep(settings.SOP_REPORTS_SYNC_INTERVAL)
