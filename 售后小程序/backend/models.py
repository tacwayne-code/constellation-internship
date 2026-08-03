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
    device_name = Column(String)
    sn_code = Column(String)
    address = Column(String)
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
