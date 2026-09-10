"""Append-only, per-order service contact snapshots. No customer-master writes."""
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator
from models import OrderContact

class ContactInput(BaseModel):
    purpose: Literal["reporting", "onsite"] = "onsite"
    name: str = Field(min_length=1, max_length=100)
    phone: str = Field(min_length=1, max_length=50)

    @field_validator("name", "phone")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("联系人和电话不能为空")
        return value.strip()


def append_contact(db, order, data, actor):
    if order.status in ("done", "rejected"):
        raise HTTPException(409, "已结束的工单保留原联系记录，不再修改")
    latest = db.query(OrderContact).filter_by(work_order_id=order.id, purpose=data.purpose).order_by(OrderContact.id.desc()).first()
    if latest and latest.name == data.name and latest.phone == data.phone:
        return
    db.add(OrderContact(work_order_id=order.id, purpose=data.purpose, name=data.name, phone=data.phone, actor=actor))


def contact_views(db, order_id):
    rows = db.query(OrderContact).filter(OrderContact.work_order_id == order_id).order_by(OrderContact.created_at, OrderContact.id).all()
    return [{"id": r.id, "purpose": r.purpose, "name": r.name, "phone": r.phone, "created_at": r.created_at} for r in rows]
