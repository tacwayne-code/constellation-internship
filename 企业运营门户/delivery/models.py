from datetime import date as Date, datetime as DateTime
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True, allow_inf_nan=False)

class Login(Model):
    username: str = Field(min_length=3, max_length=64, pattern=r'^[a-zA-Z0-9_.-]+$')
    password: str = Field(min_length=10, max_length=128)

class Account(Login):
    name: str = Field(min_length=1, max_length=40)
    admin: bool = False

class Password(Model):
    current: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=10, max_length=128)

class Milestone(Model):
    name: str = Field(min_length=1, max_length=40)
    date: Date | None = None
    status: Literal['待开始','进行中','已完成'] = '待开始'

class Project(Model):
    name: str = Field(min_length=1, max_length=100)
    location: str = Field(default='', max_length=120)
    owner: str = Field(min_length=1, max_length=40)
    due: Date | None = None
    description: str = Field(default='', max_length=3000)
    status: Literal['进行中','已完成','暂停'] = '进行中'
    milestones: list[Milestone] = Field(default_factory=list, max_length=20)

class Task(Model):
    title: str = Field(min_length=1, max_length=160)
    owner: str = Field(min_length=1, max_length=40)
    assignee_id: str | None = None
    due: DateTime | None = None
    priority: Literal['高','中','低'] = '中'
    status: Literal['待处理','处理中','已完成','已取消'] = '待处理'
    note: str = Field(default='', max_length=5000)

class Issue(Model):
    title: str = Field(min_length=1, max_length=160)
    owner: str = Field(min_length=1, max_length=40)
    priority: Literal['高','中','低'] = '中'
    status: Literal['待处理','处理中','已解决'] = '待处理'
    description: str = Field(default='', max_length=5000)
    next: str = Field(default='', max_length=1000)
    due: Date | None = None

class Document(Model):
    name: str = Field(min_length=1, max_length=160)
    type: Literal['图纸','清单','检查表','其他'] = '图纸'
    owner: str = Field(min_length=1, max_length=40)
    state: Literal['待确认','可使用','已作废'] = '待确认'
    source: str = Field(default='', max_length=200)
    note: str = Field(default='', max_length=3000)

class Purchase(Model):
    name: str = Field(min_length=1, max_length=160)
    reference: str = Field(default='', max_length=80)
    spec: str = Field(default='', max_length=200)
    qty: Decimal = Field(default=0, ge=0, le=1000000000, decimal_places=3)
    unit: str = Field(min_length=1, max_length=20)
    supplier: str = Field(default='', max_length=100)
    date: Date | None = None
    status: Literal['待确认','待发货','运输中','已到货','到货延迟','已取消'] = '待确认'
    owner: str = Field(min_length=1, max_length=40)
    note: str = Field(default='', max_length=3000)

class Inventory(Model):
    name: str = Field(min_length=1, max_length=160)
    spec: str = Field(default='', max_length=200)
    unit: str = Field(min_length=1, max_length=20)
    location: str = Field(default='', max_length=100)
    note: str = Field(default='', max_length=3000)

class Person(Model):
    name: str = Field(min_length=1, max_length=40)
    team: str = Field(default='', max_length=80)
    role: str = Field(default='', max_length=60)
    task: str = Field(default='', max_length=200)
    state: Literal['在场','已离场'] = '在场'
    note: str = Field(default='', max_length=2000)

MODELS = {'tasks':Task,'issues':Issue,'documents':Document,'purchases':Purchase,'inventory':Inventory,'people':Person}

class Edit(Model):
    version: int = Field(ge=1)
    data: dict

class Movement(Model):
    item_id: str
    kind: Literal['收料','领用','退料','盘盈','盘亏']
    qty: Decimal = Field(gt=0, le=1000000000, decimal_places=3)
    person: str = Field(min_length=1, max_length=40)
    note: str = Field(default='', max_length=2000)
    @field_validator('note')
    @classmethod
    def note_size(cls, value):
        return value

class Note(Model):
    text: str = Field(min_length=1, max_length=5000)

class Member(Model):
    user_id: str
    role: Literal['manager','member','viewer']

class UserState(Model):
    active: bool

class LinkPurchase(Model):
    order_id: int = Field(gt=0)
