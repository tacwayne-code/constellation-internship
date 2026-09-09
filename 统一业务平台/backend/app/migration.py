from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import AuditEvent, CrmActivity, Customer, Engineer, Identity, ServiceUser, WorkOrder, WorkRecord


def _dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    text = str(value or "").replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def _json_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    try:
        result = json.loads(value or "[]")
        return result if isinstance(result, list) else []
    except (TypeError, json.JSONDecodeError):
        return []


def migrate_identities(db: Session, path: Path | None = None) -> int:
    source = path or settings.identity_db
    if not source.exists():
        return 0
    count = 0
    with sqlite3.connect(source) as legacy:
        legacy.row_factory = sqlite3.Row
        tables = {row[0] for row in legacy.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "identities" not in tables:
            return 0
        for row in legacy.execute("SELECT * FROM identities"):
            identity = db.get(Identity, row["subject"])
            if not identity:
                identity = Identity(subject=row["subject"])
                db.add(identity)
            identity.display_name = row["display_name"] or "待授权员工"
            identity.crm_role = row["crm_role"] or ""
            identity.service_role = row["service_role"] or ""
            identity.status = row["status"] or "PENDING"
            identity.created_at = _dt(row["created_at"])
            identity.updated_at = _dt(row["updated_at"])
            count += 1
    db.flush()
    return count


def _primary_phone(customer: dict[str, Any]) -> str:
    contacts = customer.get("contacts") or []
    primary = next((item for item in contacts if item.get("isPrimary")), contacts[0] if contacts else {})
    return str(customer.get("phone") or primary.get("phone") or "")


def migrate_crm(db: Session, path: Path | None = None) -> dict[str, int]:
    source = path or settings.legacy_crm_json
    result = {"customers": 0, "activities": 0}
    if not source.exists():
        return result
    payload = json.loads(source.read_text(encoding="utf-8"))
    for row in payload.get("customers", []):
        legacy_id = str(row.get("id") or "")
        customer = db.scalar(select(Customer).where(Customer.legacy_id == legacy_id))
        if not customer:
            customer = Customer(legacy_id=legacy_id, name=str(row.get("name") or "未命名客户"))
            db.add(customer)
        customer.odoo_partner_id = int(row["erpCustomerId"]) if str(row.get("erpCustomerId") or "").isdigit() else None
        customer.odoo_ref = str(row.get("erpCustomerCode") or "")
        customer.name = str(row.get("name") or "未命名客户")
        customer.phone = _primary_phone(row)
        customer.email = str(row.get("email") or "")
        customer.address = str(row.get("address") or "")
        customer.owner_name = str(row.get("ownerName") or row.get("owner") or "")
        customer.source = "ODOO" if customer.odoo_partner_id else "CRM"
        customer.raw = row
        result["customers"] += 1
    for collection, activity_type in (("visits", "VISIT"), ("opportunities", "OPPORTUNITY"), ("sales", "SALE"), ("expenseReports", "EXPENSE")):
        for row in payload.get(collection, []):
            legacy_id = str(row.get("id") or "")
            existing = db.scalar(select(CrmActivity).where(CrmActivity.activity_type == activity_type, CrmActivity.legacy_id == legacy_id))
            if not existing:
                db.add(CrmActivity(activity_type=activity_type, legacy_id=legacy_id, customer_legacy_id=str(row.get("customerId") or ""), payload=row))
            else:
                existing.payload = row
            result["activities"] += 1
    db.flush()
    return result


def migrate_ass(db: Session, path: Path | None = None) -> dict[str, int]:
    source = path or settings.legacy_ass_db
    result = {"users": 0, "engineers": 0, "workOrders": 0, "workRecords": 0}
    if not source.exists():
        return result
    with sqlite3.connect(source) as legacy:
        legacy.row_factory = sqlite3.Row
        tables = {row[0] for row in legacy.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        user_map: dict[int, int] = {}
        if "users" in tables:
            for row in legacy.execute("SELECT id, username, role, name, phone FROM users"):
                item = db.scalar(select(ServiceUser).where(ServiceUser.legacy_id == row["id"]))
                if not item:
                    item = ServiceUser(legacy_id=row["id"])
                    db.add(item)
                item.username, item.role, item.name, item.phone = row["username"] or "", row["role"] or "", row["name"] or "", row["phone"] or ""
                db.flush()
                user_map[row["id"]] = item.id
                result["users"] += 1
        engineer_map: dict[int, int] = {}
        if "engineers" in tables:
            for row in legacy.execute("SELECT id, user_id, name, phone, department, specialty, status FROM engineers"):
                item = db.scalar(select(Engineer).where(Engineer.legacy_id == row["id"]))
                if not item:
                    item = Engineer(legacy_id=row["id"])
                    db.add(item)
                item.user_id = user_map.get(row["user_id"])
                item.name, item.phone = row["name"] or "", row["phone"] or ""
                item.department, item.specialty, item.status = row["department"] or "", row["specialty"] or "", row["status"] or "active"
                db.flush()
                engineer_map[row["id"]] = item.id
                result["engineers"] += 1
        work_map: dict[int, int] = {}
        if "work_orders" in tables:
            for row in legacy.execute("SELECT * FROM work_orders"):
                item = db.scalar(select(WorkOrder).where(WorkOrder.legacy_id == row["id"]))
                if not item:
                    item = WorkOrder(legacy_id=row["id"], order_no=row["order_no"])
                    db.add(item)
                for field in ("order_no", "customer_name", "customer_phone", "device_name", "sn_code", "address", "fault_type", "fault_desc", "status"):
                    if field in row.keys():
                        setattr(item, field, row[field] or "")
                item.engineer_id = engineer_map.get(row["engineer_id"])
                item.created_by_id = user_map.get(row["created_by"])
                item.raw = {key: row[key] for key in row.keys() if key not in {"fault_images"}}
                item.created_at, item.updated_at = _dt(row["created_at"]), _dt(row["updated_at"])
                db.flush()
                work_map[row["id"]] = item.id
                result["workOrders"] += 1
        if "work_records" in tables:
            for row in legacy.execute("SELECT * FROM work_records"):
                mapped_order = work_map.get(row["work_order_id"])
                if not mapped_order:
                    continue
                item = db.scalar(select(WorkRecord).where(WorkRecord.legacy_id == row["id"]))
                if not item:
                    item = WorkRecord(legacy_id=row["id"], work_order_id=mapped_order)
                    db.add(item)
                item.check_in_location = row["check_in_location"] or ""
                item.start_time, item.end_time = row["start_time"] or "", row["end_time"] or ""
                item.analysis, item.images = row["analysis"] or "", _json_list(row["images"])
                item.submitted_at = _dt(row["submitted_at"])
                result["workRecords"] += 1
    db.flush()
    return result


def migrate_all(db: Session, actor: str = "system") -> dict[str, Any]:
    summary = {"identities": migrate_identities(db), "crm": migrate_crm(db), "ass": migrate_ass(db)}
    db.add(AuditEvent(actor=actor, action="MIGRATE_LEGACY", target_type="platform", detail=summary))
    db.commit()
    return summary

