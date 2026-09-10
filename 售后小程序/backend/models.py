from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from database import Base
from datetime import datetime


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password_hash = Column(String)
    role = Column(String)  # paidan, engineer
    name = Column(String)
    phone = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)


class Engineer(Base):
    __tablename__ = "engineers"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    name = Column(String)
    phone = Column(String)
    department = Column(String)
    specialty = Column(String)
    status = Column(String, default="active")

    user = relationship("User", back_populates="engineer")


User.engineer = relationship("Engineer", uselist=False, back_populates="user")


class WorkOrder(Base):
    __tablename__ = "work_orders"

    id = Column(Integer, primary_key=True, index=True)
    order_no = Column(String, unique=True, index=True)
    customer_name = Column(String)
    customer_phone = Column(String)  # 客户联系电话（选 Odoo 客户时自动带出，可手动编辑）
    device_name = Column(String)
    sn_code = Column(String)
    address = Column(String)
    odoo_partner_id = Column(String, index=True)  # 客户来源：Odoo res.partner id（可空，手动输入时为空）
    fault_type = Column(String)
    fault_desc = Column(Text)
    fault_images = Column(Text)  # JSON string of fault image URLs
    status = Column(String, default="pending")
    engineer_id = Column(Integer, ForeignKey("engineers.id"), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    engineer = relationship("Engineer")
    records = relationship("WorkRecord", order_by="WorkRecord.submitted_at")


class WorkRecord(Base):
    __tablename__ = "work_records"

    id = Column(Integer, primary_key=True, index=True)
    work_order_id = Column(Integer, ForeignKey("work_orders.id"))
    check_in_location = Column(String)
    start_time = Column(String)
    end_time = Column(String)
    analysis = Column(Text)
    images = Column(Text)  # JSON string of image URLs
    submitted_at = Column(DateTime, default=datetime.utcnow)


class Notification(Base):
    __tablename__ = "notification_outbox"

    id = Column(String, primary_key=True)
    work_order_id = Column(Integer, index=True, nullable=False)
    engineer_id = Column(Integer, nullable=False)
    actor_id = Column(Integer, nullable=False)
    status = Column(String, nullable=False, index=True)
    attempts = Column(Integer, nullable=False, default=0)
    recipient = Column(String, nullable=True)
    content = Column(Text, nullable=False)
    error_code = Column(String, nullable=True)
    message_id = Column(String, nullable=True)
    retry_actor_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    next_attempt_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ServiceRequestReceipt(Base):
    __tablename__ = "service_request_receipts"
    id = Column(String, primary_key=True)
    reporter = Column(String, nullable=False, index=True)
    reporter_name = Column(String, nullable=False)
    source_role = Column(String, nullable=False)
    payload_hash = Column(String, nullable=False)
    work_order_id = Column(Integer, ForeignKey("work_orders.id"), unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class WorkOrderEvent(Base):
    __tablename__ = "work_order_events"
    id = Column(Integer, primary_key=True)
    work_order_id = Column(Integer, ForeignKey("work_orders.id"), nullable=False, index=True)
    status = Column(String, nullable=False)
    action = Column(String, nullable=False)
    actor_id = Column(String)
    engineer_name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
