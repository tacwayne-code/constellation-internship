"""Limited-purpose reporting tickets. Never grant dispatcher or CRM access."""
import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from database import get_db
from models import ServiceRequestReceipt, WorkOrder, WorkOrderEvent
from sqlalchemy import or_

router = APIRouter()
ROLES = {"engineer", "paidan", "销售人员", "销售经理"}


def issue_ticket(subject, name, role):
    secret = os.getenv("SSO_SHARED_SECRET", "")
    if not secret:
        raise HTTPException(503, "报备身份服务未配置")
    payload = {"aud": "service_requests", "sub": subject, "name": name,
               "role": role, "exp": int(time.time()) + 600}
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=")
    signature = base64.urlsafe_b64encode(hmac.new(secret.encode(), encoded, hashlib.sha256).digest()).rstrip(b"=")
    return "v1." + encoded.decode() + "." + signature.decode()


def reporter(x_request_ticket: str = Header(default="")):
    try:
        version, encoded, signature = x_request_ticket.split(".")
        secret = os.getenv("SSO_SHARED_SECRET", "")
        expected = base64.urlsafe_b64encode(hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
        if not secret or version != "v1" or not hmac.compare_digest(signature, expected):
            raise ValueError()
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        if payload["aud"] != "service_requests" or not payload["sub"] or not payload["name"] or payload["role"] not in ROLES or not time.time() < payload["exp"] <= time.time() + 610:
            raise ValueError()
        return payload
    except Exception:
        raise HTTPException(401, "报备身份已失效，请返回原入口重新进入") from None


class Demand(BaseModel):
    request_id: uuid.UUID
    customer_name: str = Field(min_length=1, max_length=200)
    customer_phone: str = Field(min_length=1, max_length=50)
    address: str = Field(default="", max_length=500)
    device_name: str = Field(min_length=1, max_length=200)
    sn_code: str = Field(default="", max_length=100)
    fault_desc: str = Field(min_length=1, max_length=5000)

    @field_validator("customer_name", "customer_phone", "device_name", "fault_desc")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("必填项不能为空")
        return value.strip()


@router.post("/service-requests")
def submit(data: Demand, identity=Depends(reporter), db: Session = Depends(get_db)):
    subject = hashlib.sha256(identity["sub"].encode()).hexdigest()
    key = hashlib.sha256((subject + str(data.request_id)).encode()).hexdigest()
    fields = data.model_dump(exclude={"request_id"})
    payload_hash = hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def existing(row):
        if row.payload_hash != payload_hash:
            raise HTTPException(409, "该提交编号已用于不同内容，请重新进入后提交")
        order = db.get(WorkOrder, row.work_order_id)
        if not order:
            raise HTTPException(409, "该报备已被移除，请联系派单员")
        return {"id": order.id, "order_no": order.order_no, "status": order.status}

    receipt = db.get(ServiceRequestReceipt, key)
    if receipt:
        return existing(receipt)
    order = WorkOrder(**fields, order_no="SR-" + uuid.uuid4().hex[:16].upper(),
                      status="pending", engineer_id=None, created_by=None,
                      fault_type="需求报备", fault_images="[]")
    db.add(order)
    db.flush()
    db.add(WorkOrderEvent(work_order_id=order.id, status="pending", action="提交需求", actor_id=subject))
    db.add(ServiceRequestReceipt(id=key, reporter=subject, reporter_name=identity["name"],
        source_role=identity["role"], payload_hash=payload_hash, work_order_id=order.id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        receipt = db.get(ServiceRequestReceipt, key)
        if not receipt:
            raise HTTPException(409, "提交冲突，请重试") from None
        return existing(receipt)
    return {"id": order.id, "order_no": order.order_no, "status": "pending"}


def own_orders(db, identity):
    subject = hashlib.sha256(identity["sub"].encode()).hexdigest()
    own = ServiceRequestReceipt.reporter == subject
    if identity["sub"].startswith("ass:") and identity["sub"][4:].isdigit():
        own = or_(own, WorkOrder.created_by == int(identity["sub"][4:]))
    return db.query(WorkOrder).outerjoin(ServiceRequestReceipt,
        WorkOrder.id == ServiceRequestReceipt.work_order_id).filter(own)


def summary(order):
    return {"id": order.id, "order_no": order.order_no, "customer_name": order.customer_name,
            "device_name": order.device_name, "status": order.status,
            "engineer_name": order.engineer.name if order.engineer else "",
            "created_at": order.created_at, "updated_at": order.updated_at}


@router.get("/service-requests")
def my_requests(identity=Depends(reporter), db: Session = Depends(get_db),
                offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=100)):
    query = own_orders(db, identity)
    return {"items": [summary(order) for order in query.order_by(WorkOrder.created_at.desc(), WorkOrder.id.desc()).offset(offset).limit(limit)],
            "total": query.count(), "offset": offset, "limit": limit}


@router.get("/service-requests/{order_id}")
def request_detail(order_id: int, identity=Depends(reporter), db: Session = Depends(get_db)):
    order = own_orders(db, identity).filter(WorkOrder.id == order_id).first()
    if not order:
        raise HTTPException(404, "工单不存在或不属于本人提交")
    events = db.query(WorkOrderEvent).filter(WorkOrderEvent.work_order_id == order.id).order_by(WorkOrderEvent.created_at, WorkOrderEvent.id).all()
    timeline = [{"status": e.status, "action": e.action, "at": e.created_at, "engineer_name": e.engineer_name} for e in events]
    complete = bool(events and events[0].action in ("提交需求", "创建并派单"))
    if not complete:
        timeline.insert(0, {"status": "", "action": "工单创建（历史记录）", "at": order.created_at})
    return {**summary(order), "address": order.address, "fault_desc": order.fault_desc,
            "sn_code": order.sn_code, "timeline": timeline, "history_complete": complete,
            "records": [{"submitted_at": r.submitted_at, "start_time": r.start_time,
                         "end_time": r.end_time, "analysis": r.analysis} for r in order.records]}
