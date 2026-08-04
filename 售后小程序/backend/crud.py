from sqlalchemy.orm import Session, selectinload
from sqlalchemy import or_
from models import User, Engineer, WorkOrder, WorkRecord
from datetime import datetime, timedelta
from passlib.context import CryptContext
import json

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_user_by_username(db: Session, username: str):
    return db.query(User).filter(User.username == username).first()


def get_user_by_phone(db: Session, phone: str):
    return db.query(User).filter(User.phone == phone).first()


def update_user_profile(db: Session, user: User, data):
    user.name = data.name
    user.phone = data.phone
    if data.password:
        user.password_hash = pwd_context.hash(data.password)
    if user.engineer:
        user.engineer.name = data.name
        user.engineer.phone = data.phone
    db.commit()
    db.refresh(user)
    return user


def generate_engineer_username(db: Session):
    existing = db.query(User.username).filter(User.username.like("SH%")).all()
    max_num = 0
    for (u,) in existing:
        try:
            num = int(u[2:])
            if num > max_num:
                max_num = num
        except ValueError:
            pass
    return f"SH{max_num + 1:03d}"


def get_engineers(db: Session):
    return db.query(Engineer).all()


def get_engineer(db: Session, engineer_id: int):
    return db.query(Engineer).filter(Engineer.id == engineer_id).first()


def create_engineer(db: Session, data):
    username = generate_engineer_username(db)
    user = User(
        username=username,
        password_hash=pwd_context.hash("123456"),
        role="engineer",
        name=data.name,
        phone=data.phone
    )
    db.add(user)
    db.flush()

    engineer = Engineer(
        user_id=user.id,
        name=data.name,
        phone=data.phone,
        department=data.department,
        specialty=data.specialty
    )
    db.add(engineer)
    db.commit()
    db.refresh(engineer)
    return engineer


def delete_engineer(db: Session, engineer_id: int):
    engineer = db.query(Engineer).filter(Engineer.id == engineer_id).first()
    if engineer:
        if engineer.user:
            db.delete(engineer.user)
        db.delete(engineer)
        db.commit()
    return engineer


def update_engineer(db: Session, engineer_id: int, data):
    engineer = db.query(Engineer).filter(Engineer.id == engineer_id).first()
    if not engineer:
        return None
    for field in ("name", "phone", "department", "specialty"):
        val = getattr(data, field, None)
        if val is not None:
            setattr(engineer, field, val)
            if engineer.user and field in ("name", "phone"):
                setattr(engineer.user, field, val)
    db.commit()
    db.refresh(engineer)
    return engineer


def generate_order_no(db: Session):
    today = datetime.now().strftime("%Y%m%d")
    today_count = db.query(WorkOrder).filter(WorkOrder.order_no.like(f"WO-{today}-%")).count()
    return f"WO-{today}-{today_count + 1:03d}"


def create_work_order(db: Session, data, created_by: int):
    order = WorkOrder(
        order_no=generate_order_no(db),
        customer_name=data.customer_name,
        customer_phone=getattr(data, "customer_phone", None) or None,
        device_name=data.device_name,
        sn_code=data.sn_code,
        address=data.address,
        odoo_partner_id=getattr(data, "odoo_partner_id", None) or None,
        fault_type=data.fault_type,
        fault_desc=data.fault_desc,
        fault_images=json.dumps(data.fault_images or []),
        engineer_id=data.engineer_id,
        created_by=created_by,
        status="assigned"
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def get_work_orders(
    db: Session,
    status: str | None = None,
    keyword: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    engineer_id: int | None = None,
    skip: int = 0,
    limit: int = 50,
):
    """按条件过滤工单并分页，返回 (分页结果, 总条数)。过滤下推到 SQL，避免全量加载。"""
    # selectinload 批量加载工程师与维修记录，避免列表接口 N+1 查询
    query = db.query(WorkOrder).options(
        selectinload(WorkOrder.engineer),
        selectinload(WorkOrder.records),
    )
    if status:
        query = query.filter(WorkOrder.status == status)
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(
            or_(
                WorkOrder.order_no.like(like),
                WorkOrder.customer_name.like(like),
                WorkOrder.device_name.like(like),
                WorkOrder.sn_code.like(like),
            )
        )
    if date_from:
        query = query.filter(WorkOrder.created_at >= datetime.strptime(date_from, "%Y-%m-%d"))
    if date_to:
        query = query.filter(
            WorkOrder.created_at <= datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
        )
    if engineer_id:
        query = query.filter(WorkOrder.engineer_id == engineer_id)

    total = query.count()
    orders = (
        query.order_by(WorkOrder.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return orders, total


def get_work_order(db: Session, order_id: int):
    return (
        db.query(WorkOrder)
        .options(
            selectinload(WorkOrder.engineer),
            selectinload(WorkOrder.records),
        )
        .filter(WorkOrder.id == order_id)
        .first()
    )


def update_work_order(db: Session, order_id: int, data):
    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        return None

    for field in ("customer_name", "customer_phone", "device_name", "sn_code", "address", "fault_type", "fault_desc", "engineer_id"):
        setattr(order, field, getattr(data, field))
    order.fault_images = json.dumps(data.fault_images or [])
    order.odoo_partner_id = getattr(data, "odoo_partner_id", None) or None

    if getattr(data, "status", None):
        order.status = data.status

    db.commit()
    db.refresh(order)
    return order


def delete_work_order(db: Session, order_id: int):
    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        return None

    records = db.query(WorkRecord).filter(WorkRecord.work_order_id == order_id).all()
    for record in records:
        db.delete(record)
    db.delete(order)
    db.commit()
    return order


def get_work_orders_by_engineer(db: Session, engineer_id: int, status: str = None):
    query = (
        db.query(WorkOrder)
        .options(
            selectinload(WorkOrder.engineer),
            selectinload(WorkOrder.records),
        )
        .filter(WorkOrder.engineer_id == engineer_id)
    )
    if status:
        query = query.filter(WorkOrder.status == status)
    return query.order_by(WorkOrder.created_at.desc()).all()


def create_work_record(db: Session, order_id: int, data, engineer_id: int):
    record = WorkRecord(
        work_order_id=order_id,
        check_in_location=data.check_in_location,
        start_time=data.start_time,
        end_time=data.end_time,
        analysis=data.analysis,
        images=json.dumps(data.images or [])
    )
    db.add(record)

    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if order:
        order.status = "done"

    db.commit()
    db.refresh(record)
    return record


def update_work_order_status(db: Session, order_id: int, new_status: str):
    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        return None
    valid_transitions = {
        "assigned": ["processing"],
        "processing": ["done"],
    }
    if new_status not in valid_transitions.get(order.status, []):
        raise ValueError(f"不能从 {order.status} 变更为 {new_status}")
    order.status = new_status
    db.commit()
    db.refresh(order)
    return order
