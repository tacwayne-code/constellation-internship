import re

from pydantic import BaseModel, Field, field_validator, model_validator
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
    """登录请求：支持「手机号 + 密码」登录，同时兼容旧的「账号(工号) + 密码」登录。"""
    phone: Optional[str] = Field(default=None, max_length=20, description="登录手机号（优先）")
    username: Optional[str] = Field(default=None, max_length=50, description="兼容：账号/工号")
    password: str = Field(..., min_length=1, max_length=128)
    role: str

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: Optional[str]) -> Optional[str]:
        if value is None or value == "":
            return None
        value = str(value).strip()
        if not re.fullmatch(r"1\d{10}", value):
            raise ValueError("手机号格式不正确，应为 11 位大陆手机号")
        return value

    @model_validator(mode="after")
    def check_identifier(self):
        if not self.phone and not self.username:
            raise ValueError("手机号或账号不能为空")
        return self


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
    customer_phone: Optional[str] = Field(default=None, max_length=50, description="客户联系电话")
    device_name: str = Field(..., max_length=200)
    sn_code: Optional[str] = Field(default=None, max_length=100)
    address: Optional[str] = Field(default=None, max_length=500)
    odoo_partner_id: Optional[str] = Field(default=None, max_length=50, description="Odoo 客户(res.partner)ID")
    fault_type: str = Field(..., max_length=50)
    fault_desc: str = Field(..., max_length=5000)
    fault_images: Optional[List[str]] = []
    engineer_id: int


class WorkOrderUpdate(BaseModel):
    customer_name: str = Field(..., max_length=200)
    customer_phone: Optional[str] = Field(default=None, max_length=50, description="客户联系电话")
    device_name: str = Field(..., max_length=200)
    sn_code: Optional[str] = Field(default=None, max_length=100)
    address: Optional[str] = Field(default=None, max_length=500)
    odoo_partner_id: Optional[str] = Field(default=None, max_length=50, description="Odoo 客户(res.partner)ID")
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
    customer_phone: Optional[str] = None
    device_name: str
    sn_code: Optional[str]
    address: Optional[str] = None
    odoo_partner_id: Optional[str] = None
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
