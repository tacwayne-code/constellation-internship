from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class UserOut(BaseModel):
    id: int
    username: str
    role: str
    name: str
    phone: str

    class Config:
        from_attributes = True


class LoginRequest(BaseModel):
    username: str
    password: str
    role: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str
    role: str
    user: UserOut


class UserUpdate(BaseModel):
    name: str
    phone: str
    password: Optional[str] = None


class EngineerCreate(BaseModel):
    name: str
    phone: str
    department: str
    specialty: Optional[str] = None


class EngineerOut(BaseModel):
    id: int
    name: str
    phone: str
    department: str
    specialty: Optional[str] = None
    status: str
    login_username: Optional[str] = None

    class Config:
        from_attributes = True


class WorkOrderCreate(BaseModel):
    customer_name: str
    device_name: str
    sn_code: Optional[str] = None
    address: Optional[str] = None
    fault_type: str
    fault_desc: str
    fault_images: Optional[List[str]] = []
    engineer_id: int


class WorkOrderUpdate(BaseModel):
    customer_name: str
    device_name: str
    sn_code: Optional[str] = None
    address: Optional[str] = None
    fault_type: str
    fault_desc: str
    fault_images: Optional[List[str]] = []
    engineer_id: int
    status: Optional[str] = None


class LocationPoint(BaseModel):
    label: Optional[str] = None
    longitude: float
    latitude: float


class ReverseGeocodeRequest(BaseModel):
    point: LocationPoint


class WorkOrderOut(BaseModel):
    id: int
    order_no: str
    customer_name: str
    device_name: str
    sn_code: Optional[str]
    address: Optional[str] = None
    fault_type: str
    fault_desc: str
    fault_images: Optional[List[str]] = []
    status: str
    engineer_id: Optional[int]
    engineer_name: Optional[str] = None
    engineer_phone: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class WorkOrderList(BaseModel):
    items: List[WorkOrderOut]
    total: int = 0
    stats: dict


class WorkRecordCreate(BaseModel):
    check_in_location: Optional[str] = None
    start_time: str
    end_time: str
    analysis: str
    images: Optional[List[str]] = []


class WorkRecordOut(WorkRecordCreate):
    id: int
    work_order_id: int
    submitted_at: datetime

    class Config:
        from_attributes = True
