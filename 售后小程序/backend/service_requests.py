"""Limited-purpose reporting tickets. Never grant dispatcher or CRM access."""
import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from database import get_db
from models import ServiceRequestReceipt, WorkOrder

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


@router.get("/service-requests")
def my_requests(identity=Depends(reporter), db: Session = Depends(get_db)):
    subject = hashlib.sha256(identity["sub"].encode()).hexdigest()
    rows = db.query(ServiceRequestReceipt, WorkOrder).join(WorkOrder,
        WorkOrder.id == ServiceRequestReceipt.work_order_id).filter(
        ServiceRequestReceipt.reporter == subject).order_by(ServiceRequestReceipt.created_at.desc()).limit(50).all()
    return {"items": [{"order_no": order.order_no, "customer_name": order.customer_name,
                      "status": order.status} for receipt, order in rows]}
