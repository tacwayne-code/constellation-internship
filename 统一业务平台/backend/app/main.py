from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .config import settings
from .database import Base, engine, get_db
from .migration import migrate_all
from .models import AuditEvent, Customer, Identity, IntegrationJob, SyncRun, WorkOrder
from .odoo import OdooError, PROFILES, odoo_client
from .security import COOKIE_NAME, authenticate_admin, create_admin_session, require_admin, sign_payload


Base.metadata.create_all(bind=engine)
app = FastAPI(title="群星企业统一业务平台", version="2.0.0")


class LoginBody(BaseModel):
    username: str
    password: str


class WechatLoginBody(BaseModel):
    code: str = Field(min_length=1, max_length=256)


class IdentityUpdate(BaseModel):
    subject: str = Field(min_length=1, max_length=96)
    displayName: str = Field(min_length=1, max_length=100)
    crmRole: Literal["", "销售人员", "销售经理"] = ""
    serviceRole: Literal["", "paidan", "engineer"] = ""
    status: Literal["PENDING", "ACTIVE", "DISABLED"]


class OdooImportBody(BaseModel):
    partnerIds: list[int] = Field(default_factory=list, max_length=200)


class ContactBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    phone: str = Field(default="", max_length=64)
    email: str = Field(default="", max_length=240)
    title: str = Field(default="", max_length=120)
    isPrimary: bool = False


class CustomerWriteBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    legacy_id: str = Field(default="", alias="legacyId", max_length=64)
    name: str = Field(min_length=1, max_length=240)
    phone: str = Field(default="", max_length=64)
    mobile: str = Field(default="", max_length=64)
    email: str = Field(default="", max_length=240)
    address: str = Field(default="", max_length=1000)
    owner_name: str = Field(default="", alias="ownerName", max_length=100)
    contacts: list[ContactBody] = Field(default_factory=list, max_length=20)
    idempotency_key: str = Field(default="", alias="idempotencyKey", max_length=160)


class InternalCustomerUpsert(CustomerWriteBody):
    odoo_partner_id: int | None = Field(default=None, alias="odooPartnerId", ge=1)


class ExpenseReviewBody(BaseModel):
    decision: Literal["APPROVED", "REJECTED"]
    note: str = Field(default="", max_length=1000)


def expense_bridge(path="", body=None):
    base = os.getenv("CRM_EXPENSE_API_URL", "").rstrip("/")
    key = os.getenv("CRM_EXPENSE_ADMIN_SECRET", "")
    if not base or not key:
        raise HTTPException(503, "报销服务尚未连接，请由部署管理员配置")
    request = urllib.request.Request(base + "/api/internal/expense-reports" + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-Expense-Admin-Key": key, "Content-Type": "application/json"},
        method="PUT" if body is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        with error:
            result = json.load(error)
        raise HTTPException(error.code, result.get("message", "报销操作失败")) from error
    except (OSError, ValueError) as error:
        raise HTTPException(502, "报销服务连接失败，请刷新确认结果后再操作") from error


@app.get("/api/admin/expense-reports")
def admin_expenses(actor: str = Depends(require_admin)):
    return expense_bridge()


@app.put("/api/admin/expense-reports/{report_id}/review")
def admin_review_expense(report_id: str, body: ExpenseReviewBody, actor: str = Depends(require_admin)):
    if body.decision == "REJECTED" and not body.note.strip():
        raise HTTPException(400, "驳回时请填写原因")
    return expense_bridge("/" + urllib.parse.quote(report_id, safe="") + "/review",
                          {"decision": body.decision, "note": body.note, "reviewer": actor})


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api/") else "public, max-age=300"
    return response


def audit(db: Session, actor: str, action: str, target_type: str, target_id: str = "", detail: dict | None = None) -> None:
    db.add(AuditEvent(actor=actor, action=action, target_type=target_type, target_id=target_id, detail=detail or {}))


def identity_view(item: Identity) -> dict[str, Any]:
    return {
        "subject": item.subject,
        "subjectMasked": f"{item.subject[:6]}…{item.subject[-4:]}" if len(item.subject) > 12 else "已登记",
        "displayName": item.display_name,
        "crmRole": item.crm_role,
        "serviceRole": item.service_role,
        "status": item.status,
        "updatedAt": item.updated_at.isoformat() if item.updated_at else "",
    }


def customer_view(row: dict[str, Any], db: Session) -> dict[str, Any]:
    partner_id = int(row["id"])
    existing = db.scalar(select(Customer).where(Customer.odoo_partner_id == partner_id))
    address = " ".join(str(row.get(key) or "").strip() for key in ("street", "street2", "city") if row.get(key))
    owner = row.get("user_id") or []
    return {
        "partnerId": partner_id,
        "ref": row.get("ref") or "",
        "name": row.get("name") or "",
        "phone": row.get("phone") or row.get("mobile") or "",
        "email": row.get("email") or "",
        "address": address,
        "ownerName": owner[1] if isinstance(owner, list) and len(owner) > 1 else "",
        "writeDate": row.get("write_date") or "",
        "state": "UPDATE" if existing else "NEW",
        "raw": row,
    }


def local_customer_view(item: Customer) -> dict[str, Any]:
    sync = (item.raw or {}).get("sync", {})
    contacts = (item.raw or {}).get("contacts", [])
    if not contacts and (item.phone or item.mobile):
        contacts = [{"name": item.name, "phone": item.phone or item.mobile, "isPrimary": True}]
    stable_id = item.legacy_id or (f"ODOO-{item.odoo_partner_id}" if item.odoo_partner_id else str(item.id))
    primary_contact = next((row for row in contacts if row.get("isPrimary")), None) or next(iter(contacts), {})
    return {
        "id": stable_id,
        "legacyId": item.legacy_id or "",
        "name": item.name,
        "phone": item.phone,
        "mobile": item.mobile,
        "email": item.email,
        "address": item.address,
        "ownerName": item.owner_name,
        "owner": item.owner_name,
        "contact": primary_contact.get("name") or item.name,
        "contacts": contacts,
        "status": "正常",
        "nextFollow": "",
        "note": "",
        "erpCustomerId": str(item.odoo_partner_id or ""),
        "erpCustomerCode": item.odoo_ref,
        "erpSyncStatus": sync.get("status", "LOCAL_ONLY"),
        "erpErrorCode": sync.get("errorCode", ""),
        "active": item.active,
        "source": item.source,
        "createdAt": item.created_at.isoformat() if item.created_at else "",
        "updatedAt": item.updated_at.isoformat() if item.updated_at else "",
    }


def require_internal(request: Request) -> str:
    supplied = request.headers.get("X-Platform-Internal-Key", "")
    if not settings.platform_internal_secret:
        raise HTTPException(status_code=503, detail="内部业务桥尚未配置")
    if not supplied or not secrets.compare_digest(supplied, settings.platform_internal_secret):
        raise HTTPException(status_code=401, detail="内部业务桥认证失败")
    return urllib.parse.unquote(request.headers.get("X-CRM-Actor-Name", "CRM"))[:100] or "CRM"


def find_customer_duplicate(db: Session, body: CustomerWriteBody) -> Customer | None:
    clauses = [Customer.name == body.name.strip()]
    if body.phone:
        clauses.append(Customer.phone == body.phone.strip())
    if body.email:
        clauses.append(Customer.email == body.email.strip())
    if body.legacy_id:
        clauses.append(Customer.legacy_id == body.legacy_id.strip())
    return db.scalar(select(Customer).where(or_(*clauses)).limit(1))


def apply_customer_body(item: Customer, body: CustomerWriteBody) -> None:
    item.name = body.name.strip()
    item.phone = body.phone.strip()
    item.mobile = body.mobile.strip()
    item.email = body.email.strip()
    item.address = body.address.strip()
    item.owner_name = body.owner_name.strip()
    item.active = True
    item.raw = {**(item.raw or {}), "contacts": [contact.model_dump() for contact in body.contacts]}


def sync_customer(db: Session, item: Customer, actor: str, idempotency_key: str) -> IntegrationJob:
    key = idempotency_key.strip() or f"customer:{item.legacy_id or item.id}:{item.updated_at.isoformat()}"
    job = db.scalar(select(IntegrationJob).where(IntegrationJob.source == "ODOO", IntegrationJob.idempotency_key == key))
    if job and job.status == "SUCCESS":
        return job
    if not job:
        job = IntegrationJob(source="ODOO", operation="UPSERT_CUSTOMER", idempotency_key=key, target_type="customer", target_id=item.legacy_id or str(item.id))
        db.add(job)
    request_payload = {"name": item.name, "ref": item.legacy_id or item.odoo_ref, "phone": item.phone, "mobile": item.mobile, "email": item.email, "street": item.address, "is_company": True, "contacts": (item.raw or {}).get("contacts", [])}
    job.request_payload = request_payload
    job.status = "RUNNING"
    job.attempts = int(job.attempts or 0) + 1
    db.flush()
    try:
        result = odoo_client.upsert_customer(request_payload, item.odoo_partner_id)
        item.odoo_partner_id = int(result["partnerId"])
        item.odoo_ref = item.legacy_id or item.odoo_ref
        item.source = "CRM_ODOO"
        item.raw = {**(item.raw or {}), "sync": {"status": "SYNCED", "errorCode": ""}}
        job.status, job.response_payload, job.error_code = "SUCCESS", {"partnerId": item.odoo_partner_id, "created": bool(result.get("created"))}, ""
        audit(db, actor, "SYNC_CUSTOMER_TO_ODOO", "customer", item.legacy_id or str(item.id), {"jobId": job.id, "partnerId": item.odoo_partner_id})
    except OdooError as error:
        item.raw = {**(item.raw or {}), "sync": {"status": "FAILED", "errorCode": error.code}}
        job.status, job.response_payload, job.error_code = "FAILED", {}, error.code
        audit(db, actor, "SYNC_CUSTOMER_TO_ODOO", "customer", item.legacy_id or str(item.id), {"jobId": job.id, "errorCode": error.code})
    return job


@app.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(select(func.count()).select_from(Identity)).scalar_one()
    return {"status": "ok", "version": "2.0.0", "database": "postgresql" if settings.database_url.startswith("postgres") else "sqlite", "adminReady": settings.admin_ready, "wechatReady": settings.wechat_ready, "odooReady": settings.odoo_ready}


@app.post("/api/admin/session")
def login(body: LoginBody, response: Response, db: Session = Depends(get_db)):
    if not authenticate_admin(body.username, body.password):
        raise HTTPException(status_code=401, detail="管理员账号或密码不正确")
    response.set_cookie(COOKIE_NAME, create_admin_session(), httponly=True, secure=settings.cookie_secure, samesite="strict", max_age=8 * 3600, path="/")
    audit(db, body.username, "ADMIN_LOGIN", "session")
    db.commit()
    return {"status": "ok", "name": body.username}


@app.delete("/api/admin/session")
def logout(response: Response, actor: str = Depends(require_admin)):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"status": "ok", "name": actor}


@app.get("/api/admin/me")
def me(actor: str = Depends(require_admin)):
    return {"name": actor, "role": "系统管理员"}


@app.get("/api/admin/overview")
def overview(actor: str = Depends(require_admin), db: Session = Depends(get_db)):
    del actor
    statuses = dict(db.execute(select(Identity.status, func.count()).group_by(Identity.status)).all())
    pending_orders = db.scalar(select(func.count()).select_from(WorkOrder).where(WorkOrder.status.in_(["pending", "assigned", "processing"]))) or 0
    last_sync = db.scalar(select(SyncRun).order_by(SyncRun.started_at.desc()).limit(1))
    return {
        "identities": {"pending": statuses.get("PENDING", 0), "active": statuses.get("ACTIVE", 0), "disabled": statuses.get("DISABLED", 0), "total": sum(statuses.values())},
        "business": {"customers": db.scalar(select(func.count()).select_from(Customer)) or 0, "pendingWorkOrders": pending_orders},
        "integrations": [
            {"name": "统一数据库", "status": "HEALTHY", "detail": "PostgreSQL" if settings.database_url.startswith("postgres") else "SQLite 迁移验证"},
            {"name": "微信身份", "status": "HEALTHY" if settings.wechat_ready else "UNCONFIGURED", "detail": "已配置" if settings.wechat_ready else "等待配置"},
            {"name": "Odoo API", "status": "READY" if settings.odoo_ready else "UNCONFIGURED", "detail": "可预览同步" if settings.odoo_ready else "等待只读账号"},
        ],
        "lastSync": {"status": last_sync.status, "created": last_sync.created, "updated": last_sync.updated, "conflicts": last_sync.conflicts, "at": last_sync.started_at.isoformat()} if last_sync else None,
    }


@app.get("/api/admin/customers")
def admin_customers(query: str = "", actor: str = Depends(require_admin), db: Session = Depends(get_db)):
    del actor
    statement = select(Customer).where(Customer.active.is_(True)).order_by(Customer.updated_at.desc())
    if query.strip():
        term = f"%{query.strip()}%"
        statement = statement.where(or_(Customer.name.ilike(term), Customer.phone.ilike(term), Customer.email.ilike(term), Customer.odoo_ref.ilike(term)))
    return {"items": [local_customer_view(item) for item in db.scalars(statement.limit(200)).all()]}


def create_or_update_customer(body: CustomerWriteBody, actor: str, db: Session, odoo_partner_id: int | None = None) -> tuple[Customer, IntegrationJob]:
    item = find_customer_duplicate(db, body)
    is_new = item is None
    if not item:
        legacy_id = body.legacy_id.strip() or f"CUS-{datetime.now(timezone.utc):%Y%m%d}-{secrets.token_hex(3).upper()}"
        item = Customer(legacy_id=legacy_id, name=body.name.strip(), source="CRM")
        db.add(item)
    if odoo_partner_id and not item.odoo_partner_id:
        item.odoo_partner_id = odoo_partner_id
    apply_customer_body(item, body)
    db.flush()
    job = sync_customer(db, item, actor, body.idempotency_key)
    audit(db, actor, "CREATE_CUSTOMER" if is_new else "UPDATE_CUSTOMER", "customer", item.legacy_id or str(item.id), {"syncJobId": job.id, "syncStatus": job.status})
    db.commit()
    db.refresh(item)
    return item, job


@app.post("/api/admin/customers", status_code=201)
def admin_create_customer(body: CustomerWriteBody, actor: str = Depends(require_admin), db: Session = Depends(get_db)):
    item, job = create_or_update_customer(body, actor, db)
    return {"item": local_customer_view(item), "sync": {"status": job.status, "errorCode": job.error_code}}


@app.post("/api/internal/crm/customers/upsert", status_code=201)
def internal_upsert_customer(body: InternalCustomerUpsert, actor: str = Depends(require_internal), db: Session = Depends(get_db)):
    item, job = create_or_update_customer(body, actor, db, body.odoo_partner_id)
    return {"item": local_customer_view(item), "sync": {"status": job.status, "errorCode": job.error_code}}


@app.get("/api/internal/crm/customers")
def internal_crm_customers(actor: str = Depends(require_internal), db: Session = Depends(get_db)):
    del actor
    statement = select(Customer).where(Customer.active.is_(True)).order_by(Customer.updated_at.desc()).limit(500)
    return {"items": [local_customer_view(item) for item in db.scalars(statement).all()]}


@app.get("/api/internal/ass/customers")
def internal_ass_customers(keyword: str = Query("", max_length=100), limit: int = Query(20, ge=1, le=50), partner_id: int | None = None, actor: str = Depends(require_internal), db: Session = Depends(get_db)):
    del actor
    statement = select(Customer).where(Customer.active.is_(True), Customer.odoo_partner_id.is_not(None))
    if partner_id is not None:
        statement = statement.where(Customer.odoo_partner_id == partner_id)
    if keyword.strip():
        term = keyword.strip()
        statement = statement.where(or_(*(field.contains(term, autoescape=True) for field in (Customer.name, Customer.phone, Customer.mobile, Customer.email, Customer.odoo_ref))))
    rows = db.scalars(statement.order_by(Customer.name, Customer.id).limit(limit)).all()
    return {"items": [{"id": row.odoo_partner_id, "name": row.name, "phone": row.phone, "mobile": row.mobile, "email": row.email, "address": row.address} for row in rows], "source": "platform"}


@app.post("/api/admin/customers/{customer_id}/sync")
def retry_customer_sync(customer_id: int, actor: str = Depends(require_admin), db: Session = Depends(get_db)):
    item = db.get(Customer, customer_id)
    if not item:
        raise HTTPException(status_code=404, detail="客户不存在")
    job = sync_customer(db, item, actor, f"customer-retry:{item.id}:{secrets.token_hex(6)}")
    db.commit()
    db.refresh(item)
    return {"item": local_customer_view(item), "sync": {"status": job.status, "errorCode": job.error_code}}


@app.get("/api/admin/identities")
def identities(status: str = "", query: str = "", actor: str = Depends(require_admin), db: Session = Depends(get_db)):
    del actor
    statement = select(Identity).order_by(Identity.updated_at.desc())
    if status:
        statement = statement.where(Identity.status == status.upper())
    if query:
        term = f"%{query.strip()}%"
        statement = statement.where(or_(Identity.display_name.ilike(term), Identity.subject.ilike(term)))
    return {"items": [identity_view(item) for item in db.scalars(statement).all()]}


@app.put("/api/admin/identities")
def update_identity(body: IdentityUpdate, actor: str = Depends(require_admin), db: Session = Depends(get_db)):
    item = db.get(Identity, body.subject)
    if not item:
        raise HTTPException(status_code=404, detail="身份不存在")
    before = identity_view(item)
    item.display_name, item.crm_role, item.service_role, item.status = body.displayName.strip(), body.crmRole, body.serviceRole, body.status
    item.updated_at = datetime.now(timezone.utc)
    audit(db, actor, "UPDATE_IDENTITY", "identity", hashlib.sha256(body.subject.encode()).hexdigest()[:12], {"before": before, "after": identity_view(item)})
    db.commit()
    return identity_view(item)


@app.post("/api/admin/migrations/legacy")
def run_migration(actor: str = Depends(require_admin), db: Session = Depends(get_db)):
    return migrate_all(db, actor)


@app.get("/api/admin/odoo/customers/preview")
def preview_odoo(limit: int = Query(20, ge=1, le=200), since: str = "", actor: str = Depends(require_admin), db: Session = Depends(get_db)):
    del actor
    try:
        rows = [customer_view(row, db) for row in odoo_client.customers(limit, since)]
    except OdooError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return {"items": [{key: value for key, value in row.items() if key != "raw"} for row in rows], "summary": {"discovered": len(rows), "new": sum(row["state"] == "NEW" for row in rows), "updated": sum(row["state"] == "UPDATE" for row in rows), "conflicts": 0}}


@app.get("/api/admin/odoo/profiles/status")
def odoo_profiles(actor: str = Depends(require_admin)):
    del actor
    try:
        return {"version": odoo_client.version(), "writeEnabled": settings.odoo_write_enabled, "profiles": odoo_client.capabilities()}
    except OdooError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@app.get("/api/admin/odoo/data/{profile_key}")
def preview_odoo_profile(profile_key: str, limit: int = Query(20, ge=1, le=100), since: str = "", actor: str = Depends(require_admin)):
    del actor
    if profile_key not in PROFILES:
        raise HTTPException(status_code=404, detail="未知的 Odoo 业务数据类型")
    try:
        rows = odoo_client.read_profile(profile_key, limit=limit, since=since)
    except OdooError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return {"profile": profile_key, "label": PROFILES[profile_key].label, "items": rows, "count": len(rows)}


@app.post("/api/admin/odoo/customers/import")
def import_odoo(body: OdooImportBody, actor: str = Depends(require_admin), db: Session = Depends(get_db)):
    if not body.partnerIds:
        raise HTTPException(status_code=400, detail="请先选择要导入的客户")
    try:
        source_rows = odoo_client.search_read("res.partner", [["id", "in", body.partnerIds], ["active", "=", True], ["customer_rank", ">", 0]], ["name", "ref", "phone", "mobile", "email", "street", "street2", "city", "user_id", "write_date"], limit=len(body.partnerIds))
    except OdooError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    run = SyncRun(source="ODOO_CUSTOMERS", status="RUNNING", preview=False, discovered=len(source_rows))
    db.add(run)
    db.flush()
    for raw in source_rows:
        mapped = customer_view(raw, db)
        item = db.scalar(select(Customer).where(Customer.odoo_partner_id == mapped["partnerId"]))
        is_new = item is None
        if is_new:
            item = Customer(odoo_partner_id=mapped["partnerId"], name=mapped["name"])
            db.add(item)
            run.created += 1
        else:
            run.updated += 1
        item.odoo_ref, item.name, item.phone = mapped["ref"], mapped["name"], mapped["phone"]
        item.mobile, item.email, item.address = raw.get("mobile") or "", mapped["email"], mapped["address"]
        item.owner_name, item.source, item.odoo_write_date, item.raw = mapped["ownerName"], "ODOO", mapped["writeDate"], raw
    run.status, run.finished_at = "SUCCESS", datetime.now(timezone.utc)
    audit(db, actor, "IMPORT_ODOO_CUSTOMERS", "sync_run", str(run.id), {"partnerIds": body.partnerIds, "created": run.created, "updated": run.updated})
    db.commit()
    return {"status": run.status, "discovered": run.discovered, "created": run.created, "updated": run.updated, "conflicts": run.conflicts}


@app.get("/api/admin/audit")
def audit_events(limit: int = Query(50, ge=1, le=200), actor: str = Depends(require_admin), db: Session = Depends(get_db)):
    del actor
    items = db.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(limit)).all()
    return {"items": [{"id": item.id, "actor": item.actor, "action": item.action, "targetType": item.target_type, "targetId": item.target_id, "detail": item.detail, "result": item.result, "createdAt": item.created_at.isoformat()} for item in items]}


def exchange_wechat_code(code: str) -> str:
    if not settings.wechat_ready:
        raise HTTPException(status_code=503, detail="微信身份服务尚未配置")
    query = urllib.parse.urlencode({"appid": settings.wechat_app_id, "secret": settings.wechat_app_secret, "js_code": code, "grant_type": "authorization_code"})
    try:
        with urllib.request.urlopen(f"https://api.weixin.qq.com/sns/jscode2session?{query}", timeout=8) as response:
            payload = json.loads(response.read().decode())
    except Exception as error:
        raise HTTPException(status_code=502, detail="微信身份服务暂时不可用") from error
    if payload.get("errcode") or not payload.get("openid"):
        raise HTTPException(status_code=401, detail="微信身份校验失败")
    return str(payload["openid"])


@app.post("/identity/auth/wechat/login")
def wechat_login(body: WechatLoginBody, db: Session = Depends(get_db)):
    subject = exchange_wechat_code(body.code)
    identity = db.get(Identity, subject)
    if not identity:
        identity = Identity(subject=subject)
        db.add(identity)
        db.commit()
        db.refresh(identity)
    if identity.status != "ACTIVE":
        return {"status": "PENDING", "message": "身份已登记，等待管理员授权"}
    now = int(time.time())
    base = {"sub": subject, "name": identity.display_name, "iat": now, "exp": now + 120, "jti": secrets.token_urlsafe(18)}
    tickets, roles = {}, []
    if identity.crm_role:
        tickets["crm"] = sign_payload({**base, "aud": "crm", "role": identity.crm_role}, settings.sso_shared_secret)
        roles.append("sales_manager" if identity.crm_role == "销售经理" else "sales")
    if identity.service_role:
        tickets["after_sales"] = sign_payload({**base, "aud": "service", "role": identity.service_role}, settings.sso_shared_secret)
        roles.append(identity.service_role)
    return {"status": "AUTHORIZED", "employee": {"id": hashlib.sha256(subject.encode()).hexdigest()[:16], "name": identity.display_name}, "roles": roles, "modules": list(tickets), "tickets": tickets}


def resolve_frontend_root() -> Path:
    configured = os.getenv("FRONTEND_DIST", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    module_path = Path(__file__).resolve()
    candidates = (
        module_path.parents[1] / "frontend-dist",  # Production image: /app/frontend-dist
        module_path.parents[2] / "frontend" / "dist",  # Source checkout: frontend/dist
    )
    return next((candidate for candidate in candidates if candidate.exists()), candidates[0])


frontend_root = resolve_frontend_root()
if frontend_root.exists():
    assets = frontend_root / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        requested = (frontend_root / path).resolve()
        if path and frontend_root in requested.parents and requested.is_file():
            return FileResponse(requested)
        return FileResponse(frontend_root / "index.html")
