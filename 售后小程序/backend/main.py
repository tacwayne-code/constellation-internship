from datetime import datetime, timedelta, timezone
import json
import logging
import os
import re
import secrets
import threading
import time
from sqlalchemy import inspect, text
import urllib.error
import urllib.parse
import urllib.request
import uuid

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import OAuth2PasswordBearer
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from crud import (
    create_engineer,
    create_work_order,
    create_work_record,
    delete_engineer,
    delete_work_order,
    get_engineers,
    get_user_by_username,
    get_work_order,
    get_work_orders,
    get_work_orders_by_engineer,
    update_engineer,
    update_user_profile,
    update_work_order,
    update_work_order_status,
)
from database import Base, engine, get_db
from models import Engineer, User, WorkOrder
from schemas import (
    EngineerCreate,
    LoginRequest,
    LoginResponse,
    ReverseGeocodeRequest,
    UserOut,
    UserUpdate,
    WorkOrderCreate,
    WorkOrderList,
    WorkOrderOut,
    WorkOrderUpdate,
    WorkRecordCreate,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("aftersales-api")

APP_TITLE = os.getenv("APP_TITLE", "After-sales Service API")

# ── SECRET_KEY：缺失时不拒绝启动，自动生成临时随机密钥（重启后所有令牌失效）──
# 生产环境强烈建议显式配置固定密钥，避免重启导致全员重新登录
ENV = os.getenv("ENV", "development").strip().lower()
SECRET_KEY = os.getenv("SECRET_KEY", "").strip()
if not SECRET_KEY:
    SECRET_KEY = secrets.token_urlsafe(48)
    logger.warning(
        "SECRET_KEY 未配置，已生成临时随机密钥（本次运行有效，服务重启后所有令牌失效；"
        "生产环境请通过环境变量 SECRET_KEY 显式配置固定密钥）"
    )

ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))
AMAP_WEB_SERVICE_KEY = os.getenv("AMAP_WEB_SERVICE_KEY", "").strip()
AMAP_TIMEOUT_SECONDS = int(os.getenv("AMAP_TIMEOUT_SECONDS", "15"))
DEFAULT_CORS_ORIGINS = "http://127.0.0.1:8001,http://localhost:8001"
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", DEFAULT_CORS_ORIGINS).split(",")
    if origin.strip()
]
WEB_MOUNT_PATH = os.getenv("WEB_MOUNT_PATH", "/web")
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
WEB_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "web"))

# ── 上传安全配置 ──
UPLOAD_MAX_SIZE = int(os.getenv("UPLOAD_MAX_SIZE_MB", "10")) * 1024 * 1024
# public: /uploads 静态目录直接可访问（小程序 <image> 组件无法携带请求头，必须使用该模式）
# protected: 不挂载静态目录，改为需登录态（Bearer/Cookie）的 /api/files/{name} 下载接口
UPLOADS_ACCESS_MODE = os.getenv("UPLOADS_ACCESS_MODE", "public").strip().lower()
if UPLOADS_ACCESS_MODE not in ("public", "protected"):
    raise RuntimeError("UPLOADS_ACCESS_MODE 仅支持 public / protected")

# ── 登录限流配置 ──
LOGIN_MAX_FAILURES = int(os.getenv("LOGIN_MAX_FAILURES", "5"))
LOGIN_LOCK_MINUTES = int(os.getenv("LOGIN_LOCK_MINUTES", "15"))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0").strip().lower() in ("1", "true", "yes")
TOKEN_COOKIE_NAME = "aftersales_token"

Base.metadata.create_all(bind=engine)


def ensure_schema() -> None:
    inspector = inspect(engine)
    if "work_orders" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("work_orders")}
    if "fault_images" not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE work_orders ADD COLUMN fault_images TEXT"))


ensure_schema()

app = FastAPI(title=APP_TITLE)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data: https: http:; "
        "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'",
    )
    return response


# ── 全局异常处理：统一错误响应，不向客户端泄露堆栈信息 ──
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = []
    for item in (exc.errors() or [])[:5]:
        loc = ".".join(str(part) for part in item.get("loc", []) if part != "body")
        errors.append(f"{loc}: {item.get('msg', '参数错误')}")
    return JSONResponse(
        status_code=422,
        content={"detail": "请求参数校验失败", "errors": errors},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "服务器内部错误，请稍后重试"})


os.makedirs(UPLOAD_DIR, exist_ok=True)

if UPLOADS_ACCESS_MODE == "public":
    # public：静态目录直接可访问（小程序 <image> 组件无法携带请求头，必须使用该模式）
    app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

if os.path.isdir(WEB_DIR):
    app.mount(WEB_MOUNT_PATH, StaticFiles(directory=WEB_DIR, html=True), name="web")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
# auto_error=False：允许从 Authorization 头或 httpOnly Cookie 中读取令牌
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)


# ── 登录失败限流（内存实现；多实例部署时可替换为 Redis 实现）──
class LoginRateLimiter:
    def __init__(self, max_failures: int, lock_seconds: int) -> None:
        self.max_failures = max_failures
        self.lock_seconds = lock_seconds
        self._records: dict[str, dict] = {}
        self._lock = threading.Lock()

    def _key(self, username: str, ip: str) -> str:
        return f"{username}@{ip}"

    def check(self, username: str, ip: str) -> tuple[bool, int]:
        """返回 (是否锁定, 剩余锁定秒数)"""
        now = time.time()
        with self._lock:
            entry = self._records.get(self._key(username, ip))
            if not entry:
                return False, 0
            if entry["locked_until"] and now < entry["locked_until"]:
                return True, int(entry["locked_until"] - now)
            if entry["locked_until"]:
                self._records.pop(self._key(username, ip), None)
            return False, 0

    def record_failure(self, username: str, ip: str) -> int:
        """记录一次失败，返回剩余可用尝试次数（0 表示已触发锁定）"""
        now = time.time()
        with self._lock:
            key = self._key(username, ip)
            entry = self._records.setdefault(key, {"count": 0, "locked_until": 0.0})
            entry["count"] += 1
            if entry["count"] >= self.max_failures:
                entry["locked_until"] = now + self.lock_seconds
                entry["count"] = 0
                return 0
            return self.max_failures - entry["count"]

    def reset(self, username: str, ip: str) -> None:
        with self._lock:
            self._records.pop(self._key(username, ip), None)


login_limiter = LoginRateLimiter(LOGIN_MAX_FAILURES, LOGIN_LOCK_MINUTES * 60)


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    token: str | None = Depends(oauth2_scheme),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        token = request.cookies.get(TOKEN_COOKIE_NAME)
    if not token:
        raise credentials_exception
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError as exc:
        raise credentials_exception from exc

    user = get_user_by_username(db, username)
    if user is None:
        raise credentials_exception
    return user


if UPLOADS_ACCESS_MODE == "protected":
    # protected：关闭静态目录，改为需登录态的鉴权下载接口
    # （Web 端同源 <img> 请求会自动携带 httpOnly Cookie 完成鉴权）
    @app.get("/api/files/{filename}")
    def download_file(filename: str, current_user: User = Depends(get_current_user)):
        if not re.fullmatch(r"[0-9a-f]{32}\.(jpg|jpeg|png|webp)", filename, re.IGNORECASE):
            raise HTTPException(status_code=404, detail="文件不存在")
        filepath = os.path.join(UPLOAD_DIR, filename)
        if not os.path.isfile(filepath):
            raise HTTPException(status_code=404, detail="文件不存在")
        return FileResponse(filepath)


def seed_data(db: Session) -> None:
    if db.query(User).first():
        return

    paidan = User(
        username="PD001",
        password_hash=get_password_hash("123456"),
        role="paidan",
        name="刘主管",
        phone="138****0001",
    )
    eng_user = User(
        username="SH001",
        password_hash=get_password_hash("123456"),
        role="engineer",
        name="张售后工程师",
        phone="138****8888",
    )
    eng_user2 = User(
        username="SH002",
        password_hash=get_password_hash("123456"),
        role="engineer",
        name="李工程师",
        phone="137****9999",
    )

    db.add_all([paidan, eng_user, eng_user2])
    db.commit()

    eng1 = Engineer(
        user_id=eng_user.id,
        name="张工程师",
        phone="138****8888",
        department="华东维修一部",
        specialty="液压/机械维修",
    )
    eng2 = Engineer(
        user_id=eng_user2.id,
        name="李工程师",
        phone="137****9999",
        department="华东维修二部",
        specialty="电气控制",
    )
    eng3 = Engineer(
        name="王工程师",
        phone="135****6666",
        department="华南维修部",
        specialty="机械装配",
    )
    db.add_all([eng1, eng2, eng3])
    db.commit()


@app.on_event("startup")
def on_startup() -> None:
    db = next(get_db())
    seed_data(db)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "time": datetime.now(timezone.utc).isoformat(),
        "web_enabled": os.path.isdir(WEB_DIR),
        "web_mount_path": WEB_MOUNT_PATH,
        "location_mode": "AMAP_LIVE" if AMAP_WEB_SERVICE_KEY else "NOT_CONFIGURED",
        "uploads_mode": UPLOADS_ACCESS_MODE,
        "env": ENV,
    }


@app.post("/auth/login", response_model=LoginResponse)
def login(req: LoginRequest, request: Request, db: Session = Depends(get_db)):
    ip = client_ip(request)
    locked, remaining = login_limiter.check(req.username, ip)
    if locked:
        raise HTTPException(
            status_code=429,
            detail=f"登录失败次数过多，请 {max(1, remaining // 60 + 1)} 分钟后再试",
        )

    user = get_user_by_username(db, req.username)
    if not user or not verify_password(req.password, user.password_hash) or user.role != req.role:
        remaining_attempts = login_limiter.record_failure(req.username, ip)
        if remaining_attempts <= 0:
            detail = f"登录失败次数过多，账号已锁定 {LOGIN_LOCK_MINUTES} 分钟"
        else:
            detail = f"账号、密码或角色不正确（剩余尝试 {remaining_attempts} 次）"
        raise HTTPException(status_code=400, detail=detail)

    login_limiter.reset(req.username, ip)
    access_token = create_access_token(
        data={"sub": user.username},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )

    # 同时下发 httpOnly Cookie：Web 端同源访问无需在 localStorage 保存 Token，
    # 可有效降低 Token 被 XSS 窃取的风险
    response = JSONResponse(
        content={
            "access_token": access_token,
            "token_type": "bearer",
            "role": user.role,
            "user": UserOut.model_validate(user).model_dump(),
        }
    )
    response.set_cookie(
        key=TOKEN_COOKIE_NAME,
        value=access_token,
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        path="/",
    )
    return response


@app.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(TOKEN_COOKIE_NAME, path="/")
    return {"ok": True}


@app.put("/users/me", response_model=UserOut)
def edit_current_user(
    data: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if data.password is not None and len(data.password) < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")
    return update_user_profile(db, current_user, data)


@app.get("/engineers")
def list_engineers(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    engineers = get_engineers(db)
    return [
        {
            "id": engineer.id,
            "name": engineer.name,
            "phone": engineer.phone,
            "department": engineer.department,
            "specialty": engineer.specialty,
            "status": engineer.status,
            "login_username": engineer.user.username if engineer.user else None,
        }
        for engineer in engineers
    ]


@app.post("/engineers")
def add_engineer(
    data: EngineerCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role != "paidan":
        raise HTTPException(status_code=403, detail="无权操作")
    engineer = create_engineer(db, data)
    return {
        "id": engineer.id,
        "name": engineer.name,
        "phone": engineer.phone,
        "department": engineer.department,
        "specialty": engineer.specialty,
        "status": engineer.status,
        "login_username": engineer.user.username,
    }


@app.delete("/engineers/{engineer_id}")
def remove_engineer(
    engineer_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role != "paidan":
        raise HTTPException(status_code=403, detail="无权操作")
    delete_engineer(db, engineer_id)
    return {"ok": True}


@app.put("/engineers/{engineer_id}")
def edit_engineer(
    engineer_id: int,
    data: EngineerCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role != "paidan":
        raise HTTPException(status_code=403, detail="无权操作")
    engineer = update_engineer(db, engineer_id, data)
    if not engineer:
        raise HTTPException(status_code=404, detail="工程师不存在")
    return {
        "ok": True,
        "engineer": {
            "id": engineer.id,
            "name": engineer.name,
            "phone": engineer.phone,
            "department": engineer.department,
            "specialty": engineer.specialty,
            "status": engineer.status,
        },
    }


def enrich_order(order: WorkOrder) -> dict:
    data = {
        **order.__dict__,
        "engineer_name": order.engineer.name if order.engineer else None,
        "engineer_phone": order.engineer.phone if order.engineer else None,
    }
    data["fault_images"] = json.loads(order.fault_images) if order.fault_images else []
    for field in ("created_at", "updated_at"):
        value = data.get(field)
        if value is not None and value.tzinfo is None:
            data[field] = value.replace(tzinfo=timezone.utc).isoformat()

    records_list = order.records or []
    data["records"] = [
        {
            "id": record.id,
            "start_time": record.start_time,
            "end_time": record.end_time,
            "analysis": record.analysis,
            "images": json.loads(record.images) if record.images else [],
            "check_in_location": record.check_in_location,
            "submitted_at": record.submitted_at.isoformat() if record.submitted_at else None,
        }
        for record in records_list
    ]

    if records_list:
        last = records_list[-1]
        if last.start_time and last.end_time:
            try:
                start_value = datetime.strptime(last.start_time, "%Y-%m-%d %H:%M")
                end_value = datetime.strptime(last.end_time, "%Y-%m-%d %H:%M")
                data["duration"] = int((end_value - start_value).total_seconds() / 60)
            except ValueError:
                data["duration"] = 0
        else:
            data["duration"] = 0
    else:
        data["duration"] = 0
    return data


def fetch_amap_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "after-sales-miniprogram/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=AMAP_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail="高德位置服务暂时不可用，请稍后重试") from exc


@app.post("/api/locations/reverse-geocode")
def reverse_geocode_location(
    data: ReverseGeocodeRequest,
    current_user: User = Depends(get_current_user),
):
    if not AMAP_WEB_SERVICE_KEY:
        raise HTTPException(status_code=503, detail="未配置高德 Web 服务 Key")

    point = data.point
    if not -180 <= point.longitude <= 180 or not -90 <= point.latitude <= 90:
        raise HTTPException(status_code=400, detail="经纬度超出有效范围")

    params = urllib.parse.urlencode(
        {
            "key": AMAP_WEB_SERVICE_KEY,
            "location": f"{point.longitude:.6f},{point.latitude:.6f}",
            "radius": "300",
            "extensions": "all",
            "homeorcorp": "2",
            "output": "json",
        }
    )
    payload = fetch_amap_json(f"https://restapi.amap.com/v3/geocode/regeo?{params}")
    regeocode = payload.get("regeocode") or {}
    formatted_address = str(regeocode.get("formatted_address") or "").strip()
    if str(payload.get("status")) != "1" or not formatted_address:
        message = payload.get("info") or "没有找到当前位置地址"
        raise HTTPException(status_code=502, detail=f"高德位置解析失败：{message}")

    pois = regeocode.get("pois") or []
    nearest_poi = pois[0] if pois else {}
    component = regeocode.get("addressComponent") or {}
    return {
        "result": {
            "source": "AMAP_LIVE",
            "placeName": str(nearest_poi.get("name") or formatted_address).strip(),
            "formattedAddress": formatted_address,
            "longitude": point.longitude,
            "latitude": point.latitude,
            "province": str(component.get("province") or ""),
            "city": str(component.get("city") or ""),
            "district": str(component.get("district") or ""),
            "nearestPoiDistance": str(nearest_poi.get("distance") or ""),
        }
    }


@app.get("/workorders", response_model=WorkOrderList)
def list_work_orders(
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    all_orders = get_work_orders(db)
    orders = all_orders[skip : skip + limit]
    now = datetime.now(timezone.utc)
    pending = sum(1 for order in all_orders if order.status in ("pending", "assigned", "processing"))
    completed = sum(1 for order in all_orders if order.status == "done")
    completed_this_month = sum(
        1
        for order in all_orders
        if order.status == "done"
        and order.updated_at.year == now.year
        and order.updated_at.month == now.month
    )

    return {
        "items": [enrich_order(order) for order in orders],
        "total": len(all_orders),
        "stats": {
            "pending": pending,
            "completed": completed,
            "completed_this_month": completed_this_month,
        },
    }


@app.post("/workorders", response_model=WorkOrderOut)
def add_work_order(
    data: WorkOrderCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role != "paidan":
        raise HTTPException(status_code=403, detail="无权创建工单")
    order = create_work_order(db, data, current_user.id)
    return enrich_order(order)


@app.put("/workorders/{order_id}")
def edit_work_order(
    order_id: int,
    data: WorkOrderUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role != "paidan":
        raise HTTPException(status_code=403, detail="No permission to update work order")
    order = update_work_order(db, order_id, data)
    if not order:
        raise HTTPException(status_code=404, detail="Work order not found")
    return enrich_order(order)


@app.delete("/workorders/{order_id}")
def remove_work_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role != "paidan":
        raise HTTPException(status_code=403, detail="No permission to delete work order")
    order = delete_work_order(db, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Work order not found")
    return {"ok": True}


@app.get("/workorders/me/tasks")
def my_tasks(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user.engineer:
        raise HTTPException(status_code=400, detail="当前用户不是工程师")
    orders = get_work_orders_by_engineer(db, current_user.engineer.id)
    active = [order for order in orders if order.status != "done"]
    return [enrich_order(order) for order in active]


@app.get("/workorders/me/history")
def my_history(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user.engineer:
        raise HTTPException(status_code=400, detail="当前用户不是工程师")
    orders = get_work_orders_by_engineer(db, current_user.engineer.id, status="done")
    return [enrich_order(order) for order in orders]


@app.get("/engineers/me/profile")
def my_profile(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user.engineer:
        raise HTTPException(status_code=400, detail="当前用户不是工程师")
    engineer = current_user.engineer
    return {
        "id": engineer.id,
        "name": engineer.name,
        "phone": engineer.phone,
        "department": engineer.department,
        "specialty": engineer.specialty,
        "status": engineer.status,
        "login_username": current_user.username,
    }


@app.get("/workorders/{order_id}")
def get_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    order = get_work_order(db, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="工单不存在")
    return enrich_order(order)


@app.post("/workorders/{order_id}/records")
def add_record(
    order_id: int,
    data: WorkRecordCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not current_user.engineer:
        raise HTTPException(status_code=403, detail="无权操作")
    record = create_work_record(db, order_id, data, current_user.engineer.id)
    return {
        "id": record.id,
        "work_order_id": record.work_order_id,
        "check_in_location": record.check_in_location,
        "start_time": record.start_time,
        "end_time": record.end_time,
        "analysis": record.analysis,
        "images": json.loads(record.images) if record.images else [],
        "submitted_at": record.submitted_at,
    }


@app.post("/workorders/{order_id}/accept")
def accept_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not current_user.engineer:
        raise HTTPException(status_code=403, detail="无权操作")
    try:
        order = update_work_order_status(db, order_id, "processing")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return enrich_order(order)


def sniff_image_type(head: bytes) -> str | None:
    """根据文件头魔数识别真实图片格式，忽略客户端伪造的 content_type / 扩展名"""
    if len(head) >= 3 and head[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if len(head) >= 8 and head[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    return None


@app.post("/api/upload")
async def upload_image(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    # 1) 文件头魔数校验：拒绝任何非图片内容（HTML、可执行文件等）
    head = await file.read(12)
    ext = sniff_image_type(head)
    if ext is None:
        raise HTTPException(status_code=400, detail="仅支持 jpg/png/webp 图片文件")

    # 2) 文件名由服务端生成（UUID + 以内容判定的扩展名），不信任用户输入
    filename = f"{uuid.uuid4().hex}{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)

    # 3) 流式写入并限制总大小，防止上传大文件耗尽磁盘
    size = len(head)
    with open(filepath, "wb") as output_file:
        output_file.write(head)
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > UPLOAD_MAX_SIZE:
                output_file.close()
                try:
                    os.remove(filepath)
                except OSError:
                    pass
                raise HTTPException(
                    status_code=413,
                    detail=f"文件大小不能超过 {UPLOAD_MAX_SIZE // (1024 * 1024)}MB",
                )
            output_file.write(chunk)

    return {"url": f"/uploads/{filename}", "filename": filename}
