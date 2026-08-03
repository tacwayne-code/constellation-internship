from pydantic import BaseModel, Field, field_validator
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
    customer_name: str = Field(..., max_length=200)
    device_name: str = Field(..., max_length=200)
    sn_code: Optional[str] = Field(default=None, max_length=100)
    address: Optional[str] = Field(default=None, max_length=500)
    fault_type: str = Field(..., max_length=50)
    fault_desc: str = Field(..., max_length=5000)
    fault_images: Optional[List[str]] = []
    engineer_id: int


class WorkOrderUpdate(BaseModel):
    customer_name: str = Field(..., max_length=200)
    device_name: str = Field(..., max_length=200)
    sn_code: Optional[str] = Field(default=None, max_length=100)
    address: Optional[str] = Field(default=None, max_length=500)
    fault_type: str = Field(..., max_length=50)
    fault_desc: str = Field(..., max_length=5000)
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
    check_in_location: Optional[str] = Field(default=None, max_length=200)
    start_time: str
    end_time: str
    analysis: str
    images: Optional[List[str]] = []
    # 可选的原始定位信息：若前端提供则后端校验其合法性，防止明显越界/篡改
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_time_format(cls, value: str) -> str:
        try:
            datetime.strptime(value, "%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            raise ValueError("时间格式应为 YYYY-MM-DD HH:MM")
        return value

    @field_validator("analysis")
    @classmethod
    def validate_analysis_length(cls, value: str) -> str:
        if len(value) > 5000:
            raise ValueError("故障分析内容过长")
        return value


class WorkRecordOut(WorkRecordCreate):
    id: int
    work_order_id: int
    submitted_at: datetime

    class Config:
        from_attributes = True
