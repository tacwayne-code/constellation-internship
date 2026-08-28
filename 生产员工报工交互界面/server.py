import base64
import binascii
import hashlib
import json
import hmac
import logging
import math
import os
import re
import signal
import sqlite3
import threading
import time
import uuid
import xmlrpc.client
import zlib
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import odoo_adapter
from odoo_remaining_qty_fix import OdooRemainingQuantityFix

# ---- 加载 .env 文件 ----
_ENV_FILE = Path(__file__).resolve().parent / ".env"
if _ENV_FILE.exists():
    with open(_ENV_FILE) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _, _val = _line.partition("=")
                _key = _key.strip()
                _val = _val.strip()
                if _key not in os.environ:
                    os.environ[_key] = _val
    del _key, _val, _line, _f

# ============================================================
# 配置 & 常量
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
ODOO_URL = os.getenv("ODOO_URL", "http://x.inspiri.cn").rstrip("/")
ODOO_DB = os.getenv("ODOO_DB", "inspiri_erp")
ODOO_USER = os.getenv("ODOO_USER", "ai_test")
ODOO_PASSWORD = os.getenv("ODOO_PASSWORD", "")
MOCK_MODE = os.getenv("ODOO_MOCK_MODE", "false").lower() == "true"
LOCAL_TZ = timezone(timedelta(hours=8))
API_KEY = os.getenv("API_KEY", "").strip()
REPORT_ADMIN_API_URL = os.getenv("REPORT_ADMIN_API_URL", "").strip().rstrip("/")
REPORT_ADMIN_API_KEY = os.getenv("REPORT_ADMIN_API_KEY", "").strip()
PANEL_SESSION_COOKIE = "sop_panel_session"
PANEL_SESSION_SECRET = os.getenv("SOP_PANEL_SESSION_SECRET", "").strip() or REPORT_ADMIN_API_KEY
# Browsers commonly cap an individual cookie near 4096 bytes. Keep the
# self-contained token below that boundary and fall back to a short signed
# reference when an employee has a larger authorization set.
PANEL_SESSION_COOKIE_MAX_BYTES = 3800
try:
    PANEL_SESSION_TTL_SECONDS = max(300, int(os.getenv("SOP_PANEL_SESSION_TTL_SECONDS", "43200")))
except ValueError:
    PANEL_SESSION_TTL_SECONDS = 43200
DB_FILE = BASE_DIR / ("data.mock.db" if MOCK_MODE else "data.db")
RESET_MARKER_FILE = BASE_DIR / ".reset.marker"
WHITE_EXT = {".html", ".css", ".js", ".svg", ".ico", ".png"}


def reset_marker_stamp():
    """Return the cross-process reset marker timestamp."""
    try:
        return RESET_MARKER_FILE.stat().st_mtime_ns
    except OSError:
        return 0

# ============================================================
# 日志
# ============================================================

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(BASE_DIR / "server.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("production-dashboard")
if MOCK_MODE:
    logger.warning("=" * 60)
    logger.warning("ODOO MOCK MODE ENABLED - NO REAL ODOO WRITES")
    logger.warning("=" * 60)
else:
    logger.info(f"Odoo: {ODOO_URL}")
    logger.debug(f"Odoo 连接详情: db={ODOO_DB} user={ODOO_USER}")


def _report_admin_configured():
    return bool(REPORT_ADMIN_API_URL and REPORT_ADMIN_API_KEY)


def _panel_worker_from_identity(identity):
    """Normalize the safe identity returned by the management service."""
    if not isinstance(identity, dict):
        return None
    worker_id = str(identity.get("sourceWorkerId", "")).strip()
    name = str(identity.get("name", "")).strip()
    team = str(identity.get("departmentName", identity.get("team", ""))).strip()
    raw_codes = identity.get("operationCodes", [])
    raw_bindings = identity.get("operationBindings", [])
    raw_roles = identity.get("jobRoles", identity.get("roles", identity.get("operationGroups", [])))
    if not worker_id or not name or not isinstance(raw_codes, list):
        return None
    roles = []
    if isinstance(raw_roles, list):
        for raw_role in raw_roles:
            if not isinstance(raw_role, dict):
                continue
            role_code = str(raw_role.get("code", raw_role.get("roleCode", ""))).strip()
            role_name = str(raw_role.get("name", raw_role.get("roleName", role_code))).strip()
            if not role_code or not role_name or raw_role.get("enabled", True) is False:
                continue
            raw_ops = raw_role.get("operations", raw_role.get("processes", []))
            ops = []
            if isinstance(raw_ops, list):
                for raw_op in raw_ops:
                    if not isinstance(raw_op, dict):
                        continue
                    raw_code = str(raw_op.get("code", raw_op.get("operationCode", ""))).strip()
                    process_code = str(raw_op.get("processCode", raw_code)).strip()
                    rules = raw_op.get("woMatch") if isinstance(raw_op.get("woMatch"), dict) else {}
                    # WorkProcess.code is a management-side process identifier.
                    # Legacy migration records carry their Odoo/SOP operation
                    # code separately, so use it for authorization and retain
                    # the process code only as the report/audit snapshot.
                    op_code = str(rules.get("legacyOperationCode", "")).strip() or raw_code or process_code
                    op_name = str(raw_op.get("name", raw_op.get("operationName", op_code))).strip()
                    if not op_code or not op_name or raw_op.get("enabled", True) is False:
                        continue
                    ops.append({
                        "code": op_code, "name": op_name,
                        "processCode": process_code,
                        "processName": op_name,
                        "enabled": True,
                        "workorderNames": list(raw_op.get("workorderNames") or []),
                        "productClass": raw_op.get("productClass"),
                        "hostType": raw_op.get("hostType"),
                        "requiresBom": bool(raw_op.get("requiresBom")),
                        "woMatch": rules,
                    })
            if ops:
                roles.append({"code": role_code, "name": role_name, "enabled": True, "operations": ops})
    # When the management service provides the new position -> process grants,
    # they are the complete worker authorization.  The legacy job-title codes
    # in ``operationCodes`` describe a different namespace and can otherwise
    # filter every granted process out of the second-level selector.
    if roles:
        raw_codes = [op["code"] for role in roles for op in role.get("operations", [])]
    bindings = []
    include_bindings = isinstance(raw_bindings, list) and bool(raw_bindings)
    if isinstance(raw_bindings, list):
        for raw in raw_bindings:
            if not isinstance(raw, dict):
                continue
            code = str(raw.get("code", "")).strip()
            binding_name = str(raw.get("name", "")).strip()
            if not code or not binding_name:
                continue
            if code not in VALID_OPERATIONS and not code.startswith("worker_assembly_custom_"):
                continue
            names = raw.get("workorderNames", [binding_name])
            if not isinstance(names, list):
                names = [binding_name]
            bindings.append({
                "code": code,
                "name": binding_name,
                "workorderNames": [str(value) for value in names if str(value).strip()],
                "productClass": raw.get("productClass") or None,
                "requiresBom": bool(raw.get("requiresBom")),
                "woMatch": raw.get("woMatch") if isinstance(raw.get("woMatch"), dict) else {},
            })
    binding_by_code = {binding["code"]: binding for binding in bindings}
    for role in roles:
        for index, operation in enumerate(role.get("operations", [])):
            code = str(operation.get("code", ""))
            binding = binding_by_code.get(code)
            if binding:
                # The job-title binding contains the Odoo routing/BOM metadata;
                # the role entry carries the concrete process audit identity.
                operation = {
                    **binding,
                    "processCode": operation.get("processCode", code),
                    "processName": operation.get("processName", operation.get("name", code)),
                }
                role["operations"][index] = operation
            if code and code not in binding_by_code:
                binding_by_code[code] = {
                    "code": code, "name": operation.get("name", code),
                    "processCode": operation.get("processCode", code),
                    "processName": operation.get("processName", operation.get("name", code)),
                    "workorderNames": list(operation.get("workorderNames") or []),
                    "productClass": operation.get("productClass"),
                    "hostType": operation.get("hostType"),
                    "requiresBom": bool(operation.get("requiresBom")),
                    "woMatch": operation.get("woMatch") if isinstance(operation.get("woMatch"), dict) else {},
                }
    operation_codes = [
        str(code) for code in raw_codes
        if str(code) in VALID_OPERATIONS or str(code) in binding_by_code
    ]
    # Older management deployments do not send operationBindings. Preserve
    # their static operation behavior while still accepting custom bindings
    # from newer deployments.
    for code in operation_codes:
        if code not in binding_by_code and code in OPERATION_MAP:
            op = OPERATION_MAP[code]
            binding_by_code[code] = {
                "code": code,
                "name": op.get("name", code),
                "workorderNames": list(op.get("workorderNames") or []),
                "productClass": op.get("productClass"),
                "requiresBom": bool(op.get("name") == "组装" and op.get("productClass") in {"machine", "host"}),
            }
    bindings = [binding_by_code[code] for code in operation_codes if code in binding_by_code]
    operation_codes = [binding["code"] for binding in bindings]
    if not operation_codes:
        return None
    normalized = {
        "id": worker_id,
        "name": name,
        "team": team,
        "source": "report_admin",
        "odooEmployeeId": 0,
        "operationCodes": operation_codes,
        **({"operationBindings": bindings} if include_bindings else {}),
    }
    # Preserve the legacy identity shape when older management responses do
    # not include job roles; newer responses still expose the full hierarchy.
    if roles or isinstance(raw_roles, list) and raw_roles:
        normalized["jobRoles"] = roles
    return normalized


def authenticate_panel_account(username, password):
    """Authenticate an SOP worker against the management service over HTTP."""
    if not _report_admin_configured():
        return None, "后台账号认证服务尚未配置", HTTPStatus.SERVICE_UNAVAILABLE
    body = json.dumps({"username": username, "password": password}, ensure_ascii=False).encode("utf-8")
    request = Request(
        f"{REPORT_ADMIN_API_URL}/internal/api/v1/employee-panel-auth/",
        body,
        headers={"Content-Type": "application/json", "X-Internal-API-Key": REPORT_ADMIN_API_KEY},
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:
            payload = json.load(response)
    except HTTPError as exc:
        if exc.code in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
            return None, "账号或密码错误", HTTPStatus.UNAUTHORIZED
        logger.warning("后台账号认证请求失败: HTTP %s", exc.code)
        return None, "后台账号认证服务暂时不可用", HTTPStatus.SERVICE_UNAVAILABLE
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        logger.warning("后台账号认证请求失败: %s", exc)
        return None, "后台账号认证服务暂时不可用", HTTPStatus.SERVICE_UNAVAILABLE

    worker = _panel_worker_from_identity(payload.get("data") if isinstance(payload, dict) else None)
    if worker is None:
        return None, "该账号尚未配置可报工工序", HTTPStatus.FORBIDDEN
    return worker, "", HTTPStatus.OK


def _panel_session_token(worker):
    if not PANEL_SESSION_SECRET:
        raise RuntimeError("未配置 SOP_PANEL_SESSION_SECRET 或 REPORT_ADMIN_API_KEY")
    job_roles = worker.get("jobRoles", [])
    payload = {
        "workerId": worker["id"],
        "name": worker["name"],
        "team": worker.get("team", ""),
        "operationCodes": worker.get("operationCodes", []),
        # Enriched role operations already include binding metadata. Omitting
        # the duplicate compatibility list keeps large authorizations within
        # the browser's per-cookie size limit.
        "operationBindings": [] if job_roles else worker.get("operationBindings", []),
        "jobRoles": job_roles,
        "expiresAt": int(time.time()) + PANEL_SESSION_TTL_SECONDS,
    }
    raw_payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(zlib.compress(raw_payload, level=9)).decode("ascii").rstrip("=")
    signature = hmac.new(
        PANEL_SESSION_SECRET.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256
    ).hexdigest()
    token = f"{encoded}.{signature}"
    if len(token.encode("ascii")) > PANEL_SESSION_COOKIE_MAX_BYTES:
        # Do not raise the cookie limit: browsers reject oversized cookies.
        # Store the same normalized authorization snapshot locally and give
        # the browser a signed opaque reference instead.
        session_id = db_create_panel_session(payload)
        reference = f"v2.{session_id}"
        reference_signature = hmac.new(
            PANEL_SESSION_SECRET.encode("utf-8"), reference.encode("ascii"), hashlib.sha256
        ).hexdigest()
        token = f"{reference}.{reference_signature}"
    return token


def _panel_session_worker(token):
    if not PANEL_SESSION_SECRET or not token or "." not in token:
        return None
    if token.startswith("v2."):
        try:
            reference, signature = token.rsplit(".", 1)
            _, session_id = reference.split(".", 1)
            uuid.UUID(session_id)
        except (ValueError, TypeError):
            return None
        expected = hmac.new(
            PANEL_SESSION_SECRET.encode("utf-8"), reference.encode("ascii"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        payload = db_get_panel_session_payload(session_id)
        if not isinstance(payload, dict) or int(payload.get("expiresAt", 0)) < int(time.time()):
            return None
        return _panel_worker_from_identity({
            "sourceWorkerId": payload.get("workerId", ""),
            "name": payload.get("name", ""),
            "departmentName": payload.get("team", ""),
            "operationCodes": payload.get("operationCodes", []),
            "operationBindings": payload.get("operationBindings", []),
            "jobRoles": payload.get("jobRoles", []),
        })
    encoded, signature = token.rsplit(".", 1)
    expected = hmac.new(
        PANEL_SESSION_SECRET.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        raw_payload = base64.urlsafe_b64decode(padded.encode("ascii"))
        try:
            payload = json.loads(zlib.decompress(raw_payload).decode("utf-8"))
        except zlib.error:
            # Sessions issued before payload compression remain valid until
            # their existing expiry time.
            payload = json.loads(raw_payload.decode("utf-8"))
        if int(payload.get("expiresAt", 0)) < int(time.time()):
            return None
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error, zlib.error):
        return None
    return _panel_worker_from_identity({
        "sourceWorkerId": payload.get("workerId", ""),
        "name": payload.get("name", ""),
        "departmentName": payload.get("team", ""),
        "operationCodes": payload.get("operationCodes", []),
        "operationBindings": payload.get("operationBindings", []),
        "jobRoles": payload.get("jobRoles", []),
    })


def _report_admin_payload(report, materials):
    """Translate the existing SOP row to the management-service contract."""
    return {
        "sourceReportId": str(report["id"]),
        "idempotencyKey": str(report["idempotencyKey"]),
        "productionId": str(report.get("productionId", "")),
        "productionName": str(report.get("productionName", "")),
        "workorderId": str(report.get("workorderId", "")),
        "workerId": str(report["workerId"]),
        "workerName": str(report["workerName"]),
        "workerTeam": str(report.get("workerTeam", "")),
        "jobRoleCode": str(report.get("jobRoleCode", "")),
        "jobRoleName": str(report.get("jobRoleName", "")),
        "processCode": str(report.get("processCode", report.get("operation", ""))),
        "processName": str(report.get("processName", report.get("operationLabel", ""))),
        "operation": str(report["operation"]),
        "operationLabel": str(report["operationLabel"]),
        "orderId": str(report.get("orderId", "")),
        "orderCustomer": str(report.get("orderCustomer", "")),
        "orderProduct": str(report.get("orderProduct", "")),
        "qty": report["qty"],
        "qualified": report.get("qualified", report["qty"]),
        "hours": report.get("hours", 0),
        "remark": report.get("remark", ""),
        "date": report["date"],
        "time": report["time"],
        "syncStatus": report.get("syncStatus", "local"),
        "materialSyncStatus": report.get("materialSyncStatus", "unknown"),
        "odooReportId": report.get("odooReportId", ""),
        "odooStockMoveIds": report.get("odooStockMoveIds", "[]"),
        "odooProgressQty": report.get("odooProgressQty"),
        "errorMessage": report.get("errorMessage", ""),
        "materials": materials or [],
    }


def _post_report_admin(path, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        f"{REPORT_ADMIN_API_URL}{path}", body,
        headers={"Content-Type": "application/json", "X-Internal-API-Key": REPORT_ADMIN_API_KEY},
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:
            if response.status not in (200, 201):
                raise RuntimeError(f"unexpected status {response.status}")
        return True
    except (HTTPError, URLError, TimeoutError, OSError, RuntimeError) as exc:
        logger.warning("后台管理副本推送失败 (%s): %s", path, exc)
        return False


def _push_report_admin(report, materials, final_status=False):
    """Run outside the worker request; failure never affects SOP/Odoo processing."""
    if not _report_admin_configured():
        return
    payload = _report_admin_payload(report, materials)

    def push():
        if not _post_report_admin("/internal/api/v1/work-reports/", payload):
            return
        if final_status:
            event_fingerprint = hashlib.sha256(json.dumps({
                "syncStatus": payload["syncStatus"],
                "materialSyncStatus": payload["materialSyncStatus"],
                "odooProgressQty": payload["odooProgressQty"],
                "errorMessage": payload["errorMessage"],
            }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:24]
            _post_report_admin("/internal/api/v1/work-reports/sync-status/", {
                "sourceReportId": payload["sourceReportId"],
                "idempotencyKey": payload["idempotencyKey"],
                "eventKey": f"{payload['sourceReportId']}:final:{event_fingerprint}",
                "syncStatus": payload["syncStatus"],
                "materialSyncStatus": payload["materialSyncStatus"],
                "odooReportId": payload["odooReportId"],
                "odooStockMoveIds": payload["odooStockMoveIds"],
                "odooProgressQty": payload["odooProgressQty"],
                "errorMessage": payload["errorMessage"],
            })

    threading.Thread(target=push, name="report-admin-sync", daemon=True).start()

# ============================================================
# 并发锁
# ============================================================

DB_LOCK = threading.Lock()
ODOO_LOCK = threading.Lock()
MATERIAL_QUANTITY_LOCK = threading.Lock()

# ============================================================
# Odoo 客户端
# ============================================================

class OdooError(RuntimeError):
    pass


class OdooClient:
    def __init__(self):
        self._uid = None
        import socket
        # 设置 30 秒超时避免 Odoo 挂死时阻塞（单进程服务，无副作用）
        if not hasattr(OdooClient, "_timeout_set"):
            socket.setdefaulttimeout(30)
            OdooClient._timeout_set = True
        self.common = xmlrpc.client.ServerProxy(
            f"{ODOO_URL}/xmlrpc/2/common", allow_none=True
        )
        self.models = xmlrpc.client.ServerProxy(
            f"{ODOO_URL}/xmlrpc/2/object", allow_none=True
        )

    def authenticate(self):
        if not ODOO_PASSWORD:
            raise OdooError("缺少 ODOO_PASSWORD")
        uid = self.common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASSWORD, {})
        if not uid:
            raise OdooError("Odoo 登录失败")
        self._uid = uid
        return uid

    def call(self, model, method, args=None, kwargs=None):
        with ODOO_LOCK:
            if self._uid is None:
                self.authenticate()
            return self.models.execute_kw(
                ODOO_DB, self._uid, ODOO_PASSWORD, model, method,
                args or [], kwargs or {},
            )

    def search_read(self, model, domain, fields, limit=100, order=None):
        kw = {"fields": fields, "limit": limit}
        if order:
            kw["order"] = order
        return self.call(model, "search_read", [domain], kw)

    def read(self, model, ids, fields):
        if not ids:
            return []
        return self.call(model, "read", [ids], {"fields": fields})


# ============================================================
# 工厂函数 - 根据 Mock 模式选择 Odoo 客户端
# ============================================================

_odoo_client = None
_odoo_mode = None

def get_odoo():
    global _odoo_client, _odoo_mode
    if _odoo_client is None:
        _odoo_client, _odoo_mode, _ = odoo_adapter.create_odoo_client(
            OdooClient, OdooError
        )
        if _odoo_mode == "mock":
            logger.warning("FakeOdooClient 已激活 - 模拟模式")
    return _odoo_client

def get_odoo_mode():
    global _odoo_mode
    if _odoo_mode is None:
        get_odoo()
    return _odoo_mode or ("mock" if MOCK_MODE else "real")


def odoo_call(client, model, method, args=None, kwargs=None):
    """Call either the project client wrapper or an execute_kw-compatible client."""
    call = getattr(client, "call", None)
    if callable(call):
        return call(model, method, args, kwargs)
    execute_kw = getattr(client, "execute_kw", None)
    if callable(execute_kw):
        uid = getattr(client, "_uid", None)
        if uid is None and hasattr(client, "authenticate"):
            uid = client.authenticate()
        if uid is None:
            raise OdooError("Odoo client is not authenticated")
        return execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD, model, method,
            args or [], kwargs or {},
        )
    raise TypeError("Unsupported Odoo client: missing call/execute_kw")

# ============================================================
# SQLite 数据层
# ============================================================

def _migrate_db():
    """可重复执行的 SQLite 迁移"""
    with DB_LOCK:
        conn = sqlite3.connect(str(DB_FILE))
        conn.execute("PRAGMA journal_mode=WAL")
        cursor = conn.cursor()

        # workers 表迁移
        existing_wcols = {row[1] for row in cursor.execute("PRAGMA table_info(workers)").fetchall()}
        worker_migrations = [
            ("source", "TEXT DEFAULT 'local'"),
            ("odoo_employee_id", "INTEGER DEFAULT 0"),
            ("operation_codes", "TEXT DEFAULT '[]'"),
            ("job_roles", "TEXT DEFAULT '[]'"),
        ]
        for col_name, col_def in worker_migrations:
            if col_name not in existing_wcols:
                cursor.execute(f"ALTER TABLE workers ADD COLUMN {col_name} {col_def}")
                logger.info(f"迁移: workers 表新增字段 {col_name}")

        # reports 表迁移
        existing_cols = {row[1] for row in cursor.execute("PRAGMA table_info(reports)").fetchall()}

        migrations = [
            ("production_id", "TEXT DEFAULT ''"),
            ("workorder_id", "TEXT DEFAULT ''"),
            ("odoo_employee_id", "INTEGER DEFAULT 0"),
            ("idempotency_key", "TEXT DEFAULT ''"),
            ("odoo_report_id", "TEXT DEFAULT ''"),
            ("odoo_stock_move_ids", "TEXT DEFAULT ''"),
            ("sync_status", "TEXT DEFAULT 'local'"),
            ("material_sync_status", "TEXT DEFAULT 'unknown'"),
            ("odoo_progress_qty", "REAL DEFAULT NULL"),
            ("error_message", "TEXT DEFAULT ''"),
            ("job_role_code", "TEXT DEFAULT ''"),
            ("job_role_name", "TEXT DEFAULT ''"),
            ("process_code", "TEXT DEFAULT ''"),
            ("process_name", "TEXT DEFAULT ''"),
        ]

        for col_name, col_def in migrations:
            if col_name not in existing_cols:
                cursor.execute(f"ALTER TABLE reports ADD COLUMN {col_name} {col_def}")
                logger.info(f"迁移: reports 表新增字段 {col_name}")

        # 创建 report_materials 表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS report_materials (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id  TEXT NOT NULL,
                product_id INTEGER NOT NULL,
                bom_line_id INTEGER DEFAULT 0,
                default_code TEXT DEFAULT '',
                actual_qty  REAL NOT NULL CHECK(actual_qty > 0),
                uom_id     INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (report_id) REFERENCES reports(id)
            )
        """)

        # idempotency_key 唯一索引
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_reports_idempotency
            ON reports(idempotency_key)
            WHERE idempotency_key != ''
        """)

        # Reports may be submitted multiple times for the same worker and day.
        # Keep this as a lookup index only; request-level deduplication is
        # enforced exclusively by idx_reports_idempotency.
        worker_date_index = next(
            (row for row in cursor.execute("PRAGMA index_list('reports')").fetchall()
             if row[1] == "idx_reports_worker_date"),
            None,
        )
        worker_date_columns = []
        if worker_date_index:
            worker_date_columns = [
                row[2] for row in cursor.execute(
                    "PRAGMA index_info('idx_reports_worker_date')"
                ).fetchall()
            ]
        if (worker_date_index is None
                or bool(worker_date_index[2])
                or worker_date_columns != ["worker_id", "date"]):
            cursor.execute("DROP INDEX IF EXISTS idx_reports_worker_date")
            cursor.execute("""
                CREATE INDEX idx_reports_worker_date
                ON reports(worker_id, date)
            """)

        conn.commit()
        conn.close()
    logger.info(f"SQLite 迁移完成 (DB: {DB_FILE})")


def _init_db():
    """初始化 SQLite 数据库和表结构"""
    with DB_LOCK:
        conn = sqlite3.connect(str(DB_FILE))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS workers (
                id    TEXT PRIMARY KEY,
                name  TEXT NOT NULL,
                team  TEXT DEFAULT '',
                source TEXT DEFAULT 'local',
                odoo_employee_id INTEGER DEFAULT 0,
                operation_codes TEXT DEFAULT '[]',
                job_roles TEXT DEFAULT '[]',
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS reports (
                id         TEXT PRIMARY KEY,
                worker_id  TEXT NOT NULL,
                worker_name TEXT NOT NULL,
                worker_team TEXT DEFAULT '',
                order_id   TEXT NOT NULL,
                order_customer TEXT DEFAULT '',
                order_product  TEXT DEFAULT '',
                operation  TEXT NOT NULL,
                operation_label TEXT NOT NULL,
                qty        INTEGER NOT NULL CHECK(qty > 0),
                qualified  INTEGER NOT NULL DEFAULT 0,
                hours      REAL NOT NULL DEFAULT 0,
                remark     TEXT DEFAULT '',
                date       TEXT NOT NULL,
                time       TEXT NOT NULL,
                timestamp  INTEGER NOT NULL,
                production_id TEXT DEFAULT '',
                workorder_id  TEXT DEFAULT '',
                 odoo_employee_id INTEGER DEFAULT 0,
                 idempotency_key TEXT DEFAULT '',
                 odoo_report_id  TEXT DEFAULT '',
                 odoo_stock_move_ids TEXT DEFAULT '',
                 sync_status TEXT DEFAULT 'local',
                 material_sync_status TEXT DEFAULT 'unknown',
                 odoo_progress_qty REAL DEFAULT NULL,
                 error_message TEXT DEFAULT '',
                 job_role_code TEXT DEFAULT '',
                 job_role_name TEXT DEFAULT '',
                 process_code TEXT DEFAULT '',
                 process_name TEXT DEFAULT '',
                 created_at TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_reports_date ON reports(date);
            CREATE INDEX IF NOT EXISTS idx_reports_worker_date ON reports(worker_id, date);

            -- ESOP：SOP 查看日志
            CREATE TABLE IF NOT EXISTS sop_view_logs (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                attachment_id  TEXT NOT NULL,
                attachment_name TEXT DEFAULT '',
                worker_id      TEXT DEFAULT '',
                worker_name    TEXT DEFAULT '',
                workorder_id   TEXT DEFAULT '',
                viewed_at      TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_sop_logs_wo ON sop_view_logs(workorder_id);

            CREATE TABLE IF NOT EXISTS panel_sessions (
                session_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_panel_sessions_expires ON panel_sessions(expires_at);

        """)
        conn.commit()
        conn.close()
    logger.info("SQLite 数据库初始化完成")
    # 执行迁移
    _migrate_db()


def db_create_panel_session(payload):
    """Persist an oversized panel session payload and return its opaque id."""
    session_id = str(uuid.uuid4())
    expires_at = int(payload["expiresAt"])
    encoded_payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    with DB_LOCK:
        conn = sqlite3.connect(str(DB_FILE))
        try:
            # Bound storage growth while retaining all still-valid sessions.
            conn.execute("DELETE FROM panel_sessions WHERE expires_at < ?", (int(time.time()),))
            conn.execute(
                "INSERT INTO panel_sessions (session_id, payload, expires_at) VALUES (?, ?, ?)",
                (session_id, encoded_payload, expires_at),
            )
            conn.commit()
        finally:
            conn.close()
    return session_id


def db_get_panel_session_payload(session_id):
    """Return a non-expired oversized panel session payload, if present."""
    with DB_LOCK:
        conn = sqlite3.connect(str(DB_FILE))
        try:
            row = conn.execute(
                "SELECT payload FROM panel_sessions WHERE session_id = ? AND expires_at >= ?",
                (session_id, int(time.time())),
            ).fetchone()
        finally:
            conn.close()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        logger.warning("面板会话数据无效: %s", session_id)
        return None


def _seed_workers():
    """Ensure the one locally managed host-computer worker exists."""
    with DB_LOCK:
        conn = sqlite3.connect(str(DB_FILE))
        count = conn.execute("SELECT COUNT(*) FROM workers").fetchone()[0]
        if count == 0:
            default = [
                ("LOCAL_LWH", "罗伟华", "组装班", "local", 0,
                 '["pc_assembly_tape", "pc_assembly_splitter"]'),
            ]
            conn.executemany(
                "INSERT INTO workers "
                "(id, name, team, source, odoo_employee_id, operation_codes) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                default
            )
            conn.commit()
            logger.info("已写入本地工人罗伟华")
        else:
            # 确保罗伟华存在（如果没有的话）
            existing = conn.execute("SELECT id FROM workers WHERE name = '罗伟华'").fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO workers "
                    "(id, name, team, source, odoo_employee_id, operation_codes) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    ("LOCAL_LWH", "罗伟华", "组装班", "local", 0,
                     '["pc_assembly_tape", "pc_assembly_splitter"]')
                )
                conn.commit()
                logger.info("已添加罗伟华（本地工人）")
        conn.close()
    _ensure_worker_operation_bindings()


WORKER_OPERATION_BINDINGS = {
    "LOCAL_LWH": ["pc_assembly_tape", "pc_assembly_splitter"],
}


def _effective_worker_source(worker_id, source):
    """Keep configured local host workers distinct from mirrored employees."""
    if str(worker_id) in WORKER_OPERATION_BINDINGS:
        return "local"
    return source or "local"


def _ensure_worker_operation_bindings():
    """Persist the allowed operation codes for configured local workers."""
    with DB_LOCK:
        conn = sqlite3.connect(str(DB_FILE))
        for worker_id, operation_codes in WORKER_OPERATION_BINDINGS.items():
            encoded = json.dumps(operation_codes, ensure_ascii=False)
            conn.execute(
                "UPDATE workers SET operation_codes=? "
                "WHERE id=? AND (operation_codes IS NULL OR trim(operation_codes) IN ('', '[]'))",
                (encoded, worker_id),
            )
        conn.commit()
        conn.close()


def db_workers():
    with DB_LOCK:
        c = sqlite3.connect(str(DB_FILE))
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT id, name, team, source, odoo_employee_id, operation_codes, job_roles "
            "FROM workers ORDER BY id"
        ).fetchall()
        c.close()
    results = []
    for r in rows:
        try:
            operation_codes = json.loads(r["operation_codes"] or "[]")
        except (TypeError, ValueError):
            operation_codes = []
        if not isinstance(operation_codes, list):
            operation_codes = []
        try:
            job_roles = json.loads(r["job_roles"] or "[]")
        except (TypeError, ValueError):
            job_roles = []
        if not isinstance(job_roles, list):
            job_roles = []
        w = {"id": r["id"], "name": r["name"], "team": r["team"],
             "source": _effective_worker_source(
                 r["id"], r["source"] if "source" in r.keys() else "local"
             ),
             "odooEmployeeId": r["odoo_employee_id"] if "odoo_employee_id" in r.keys() else 0,
             "operationCodes": [str(code) for code in operation_codes],
             "jobRoles": job_roles}
        results.append(w)
    return results


def db_add_worker(wid, name, team, source="local", odoo_employee_id=0,
                  operation_codes=None):
    operation_codes = operation_codes or []
    with DB_LOCK:
        c = sqlite3.connect(str(DB_FILE))
        c.execute(
            "INSERT INTO workers "
            "(id, name, team, source, odoo_employee_id, operation_codes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (wid, name, team, source, odoo_employee_id,
             json.dumps(operation_codes, ensure_ascii=False))
        )
        c.commit()
        c.close()
    logger.info(f"添加工人: {name} ({wid}), source={source}")


def db_upsert_worker(wid, name, team, source="report_admin", operation_codes=None, job_roles=None):
    operation_codes = operation_codes or []
    with DB_LOCK:
        c = sqlite3.connect(str(DB_FILE))
        c.execute(
            """INSERT INTO workers (id, name, team, source, odoo_employee_id, operation_codes, job_roles)
               VALUES (?, ?, ?, ?, 0, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 name=excluded.name,
                 team=excluded.team,
                 source=excluded.source,
                 odoo_employee_id=0,
                 operation_codes=excluded.operation_codes,
                 job_roles=excluded.job_roles""",
            (wid, name, team, source, json.dumps(operation_codes, ensure_ascii=False),
             json.dumps(job_roles or [], ensure_ascii=False)),
        )
        c.commit()
        c.close()


def _normalize_report(row):
    """将 SQLite 字段转为前端格式（兼容 snake_case + camelCase）"""
    base = {
        "id": row["id"], "workerId": row["worker_id"], "workerName": row["worker_name"],
        "workerTeam": row.get("worker_team", ""),
        "orderId": row["order_id"], "orderCustomer": row.get("order_customer", ""),
        "orderProduct": row.get("order_product", ""),
        "operation": row["operation"], "operationLabel": row["operation_label"],
        "jobRoleCode": row.get("job_role_code", ""), "jobRoleName": row.get("job_role_name", ""),
        "processCode": row.get("process_code", row["operation"]), "processName": row.get("process_name", row["operation_label"]),
        "qty": row["qty"], "qualified": row["qualified"], "hours": row["hours"],
        "remark": row.get("remark", ""), "date": row["date"], "time": row["time"],
        "timestamp": row["timestamp"],
    }
    # 新字段（同时输出 snake_case 和 camelCase 以兼容前端）
    for f in ["production_id", "workorder_id", "odoo_employee_id", "idempotency_key",
              "odoo_report_id", "odoo_stock_move_ids", "sync_status",
              "material_sync_status", "odoo_progress_qty", "error_message"]:
        if f in row.keys():
            base[f] = row[f]
    # camelCase 别名（前端使用）
    base["productionId"] = row.get("production_id", "")
    base["workorderId"] = row.get("workorder_id", "")
    base["odooEmployeeId"] = row.get("odoo_employee_id", 0)
    base["idempotencyKey"] = row.get("idempotency_key", "")
    base["odooReportId"] = row.get("odoo_report_id", "")
    base["odooStockMoveIds"] = row.get("odoo_stock_move_ids", "")
    base["syncStatus"] = row.get("sync_status", "")
    base["materialSyncStatus"] = row.get("material_sync_status", "unknown")
    base["odooProgressQty"] = row.get("odoo_progress_qty")
    base["errorMessage"] = row.get("error_message", "")
    base["odooDisplayOnly"] = bool(row.get("odoo_display_only", False))
    return base


REPORT_COLS = ["id", "worker_id", "worker_name", "worker_team", "order_id",
               "order_customer", "order_product", "operation", "operation_label",
               "qty", "qualified", "hours", "remark", "date", "time", "timestamp",
               "production_id", "workorder_id", "odoo_employee_id",
               "idempotency_key", "odoo_report_id", "odoo_stock_move_ids",
               "sync_status", "material_sync_status", "odoo_progress_qty",
               "error_message", "job_role_code", "job_role_name", "process_code", "process_name", "created_at"]


def db_reports(date_filter=None, limit=500):
    with DB_LOCK:
        c = sqlite3.connect(str(DB_FILE))
        c.row_factory = sqlite3.Row
        if date_filter:
            rows = c.execute(
                "SELECT * FROM reports WHERE date = ? ORDER BY timestamp DESC LIMIT ?",
                (date_filter, limit)
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM reports ORDER BY timestamp DESC LIMIT ?",
                (limit,)
            ).fetchall()
        c.close()
    return [dict(r) for r in rows]


def db_get_report(rid):
    with DB_LOCK:
        c = sqlite3.connect(str(DB_FILE))
        c.row_factory = sqlite3.Row
        row = c.execute("SELECT * FROM reports WHERE id = ?", (rid,)).fetchone()
        c.close()
    return dict(row) if row else None


def db_get_report_by_idempotency(key):
    """根据幂等键查询报工"""
    with DB_LOCK:
        c = sqlite3.connect(str(DB_FILE))
        c.row_factory = sqlite3.Row
        row = c.execute(
            "SELECT * FROM reports WHERE idempotency_key = ? AND idempotency_key != ''",
            (key,)
        ).fetchone()
        c.close()
    return dict(row) if row else None


def db_update_report_sync(report_id, sync_status, error_message="",
                          odoo_report_id=None, odoo_stock_move_ids=None,
                          material_sync_status=None, odoo_progress_qty=None):
    """Update Odoo synchronization fields without inserting a second report."""
    assignments = ["sync_status = ?", "error_message = ?"]
    values = [sync_status, error_message or ""]
    optional_values = {
        "odoo_report_id": odoo_report_id,
        "odoo_stock_move_ids": odoo_stock_move_ids,
        "material_sync_status": material_sync_status,
        "odoo_progress_qty": odoo_progress_qty,
    }
    for column, value in optional_values.items():
        if value is not None:
            assignments.append(f"{column} = ?")
            values.append(value)
    values.append(report_id)
    with DB_LOCK:
        c = sqlite3.connect(str(DB_FILE))
        try:
            cursor = c.execute(f"UPDATE reports SET {', '.join(assignments)} WHERE id = ?", values)
            updated = cursor.rowcount == 1
            c.commit()
        finally:
            c.close()
    return updated


def db_add_report(report, materials=None):
    """添加报工，若违反唯一约束则返回 False"""
    with DB_LOCK:
        c = sqlite3.connect(str(DB_FILE))
        try:
            c.execute(
                """INSERT INTO reports
                (id, worker_id, worker_name, worker_team, order_id, order_customer,
                 order_product, operation, operation_label, qty, qualified, hours,
                 remark, date, time, timestamp, production_id, workorder_id,
                 odoo_employee_id, idempotency_key, odoo_report_id,
                 odoo_stock_move_ids, sync_status, material_sync_status,
                 odoo_progress_qty, error_message, job_role_code, job_role_name,
                 process_code, process_name)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (report["id"], report["workerId"], report["workerName"],
                 report.get("workerTeam", ""), report["orderId"],
                 report.get("orderCustomer", ""), report.get("orderProduct", ""),
                 report["operation"], report["operationLabel"],
                 report["qty"], report.get("qualified", report["qty"]),
                 report.get("hours", 0), report.get("remark", ""),
                 report["date"], report["time"], report["timestamp"],
                 report.get("productionId", ""), report.get("workorderId", ""),
                 report.get("odooEmployeeId", 0), report.get("idempotencyKey", ""),
                 report.get("odooReportId", ""), report.get("odooStockMoveIds", ""),
                 report.get("syncStatus", "local"),
                 report.get("materialSyncStatus", "unknown"),
                 report.get("odooProgressQty"), report.get("errorMessage", ""),
                 report.get("jobRoleCode", ""), report.get("jobRoleName", ""),
                 report.get("processCode", report.get("operation", "")),
                 report.get("processName", report.get("operationLabel", ""))),
            )
            # 保存物料记录
            if materials:
                for mat in materials:
                    c.execute(
                        """INSERT INTO report_materials
                        (report_id, product_id, bom_line_id, default_code, actual_qty, uom_id)
                        VALUES (?, ?, ?, ?, ?, ?)""",
                        (report["id"], mat.get("productId", 0), mat.get("bomLineId", 0),
                         mat.get("defaultCode", ""), mat.get("actualQty", 1), mat.get("uomId", 1)),
                    )
            c.commit()
            ok = True
        except sqlite3.IntegrityError:
            ok = False
        c.close()
    if ok:
        logger.info(f"报工: {report['workerName']} 工单#{report.get('workorderId','')} {report['qty']}台 "
                     f"物料{len(materials) if materials else 0}项 [{report.get('syncStatus','local')}]")
    return ok


def db_get_report_materials(report_id):
    with DB_LOCK:
        c = sqlite3.connect(str(DB_FILE))
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT * FROM report_materials WHERE report_id = ?", (report_id,)
        ).fetchall()
        c.close()
    return [dict(r) for r in rows]


# ============================================================
# 数据查询函数
# ============================================================

PRODUCTION_DEPARTMENT_NAME = os.getenv("ODOO_PRODUCTION_DEPARTMENT", "生产车间").strip()
_WORKER_CACHE_TTL = 30
_WORKER_CACHE_LOCK = threading.Lock()
_WORKER_CACHE = {"data": None, "ts": 0}


def _split_job_operations(job_name):
    return [
        part.strip()
        for part in re.split(r"[、,，/;；]+", str(job_name or ""))
        if part.strip()
    ]


def _load_report_admin_workers():
    """Read the employee master data from the management service over HTTP."""
    if not _report_admin_configured():
        raise RuntimeError("未配置后台员工接口")
    request = Request(
        f"{REPORT_ADMIN_API_URL}/internal/api/v1/employees/",
        headers={"Accept": "application/json", "X-Internal-API-Key": REPORT_ADMIN_API_KEY},
        method="GET",
    )
    with urlopen(request, timeout=5) as response:
        payload = json.load(response)
    rows = payload.get("data", []) if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        raise ValueError("后台员工接口返回格式无效")
    workers = []
    for row in rows:
        # Employee-list refreshes must use the same legacy process-code
        # normalization as login. Otherwise a raw WorkProcess.code such as
        # legacy-process-* no longer matches its SOP authorization code.
        worker = _panel_worker_from_identity(row)
        if worker is None:
            continue
        workers.append({
            **worker,
            "source": _effective_worker_source(worker["id"], "report_admin"),
            "jobTitle": str(row.get("jobTitle", "")).strip(),
            "jobOperationNames": _split_job_operations(row.get("jobTitle", "")),
        })
    return workers


def load_workers(force_refresh=False):
    now = time.time()
    with _WORKER_CACHE_LOCK:
        if (not force_refresh and _WORKER_CACHE["data"] is not None
                and now - _WORKER_CACHE["ts"] < _WORKER_CACHE_TTL):
            return list(_WORKER_CACHE["data"])
    try:
        workers = _load_report_admin_workers()
        with _WORKER_CACHE_LOCK:
            _WORKER_CACHE["data"] = list(workers)
            _WORKER_CACHE["ts"] = time.time()
        return workers
    except Exception as exc:
        logger.warning(f"后台员工同步失败: {exc}")
        with _WORKER_CACHE_LOCK:
            cached = list(_WORKER_CACHE["data"] or [])
        return cached or db_workers()


def load_reports():
    reports = db_reports()
    if get_odoo_mode() != "real":
        return reports

    # Some historic reports reached Odoo before the local SQLite audit row was
    # committed.  Surface today's Odoo work-order changes for the panel, but
    # never backfill or fabricate a local report record from a cumulative WO
    # quantity.  These read-only snapshots are explicitly marked for clients.
    try:
        return reports + odoo_today_progress_snapshots(reports)
    except Exception as exc:
        logger.warning(f"读取 Odoo 当日工单进度快照失败: {exc}")
        return reports


# ============================================================
# 工序定义
# ============================================================

OPERATIONS = [
    {"id": "assembly", "code": "assembly", "name": "总装", "hostType": None,
     "workorderNames": ["组装"]},
    {"id": "testing", "code": "testing", "name": "测试", "hostType": None},
    {"id": "qc", "code": "qc", "name": "质检", "hostType": None},
    {"id": "packing", "code": "packing", "name": "包装", "hostType": None,
     "workorderNames": ["打包"]},
    {"id": "debug", "code": "debug", "name": "调试", "hostType": None,
     "productClass": "machine", "workorderNames": ["调试"]},
    # Stable replacements for the former Odoo-job-derived operation codes.
    {"id": "worker_assembly", "code": "worker_assembly", "name": "组装", "hostType": None,
     "productClass": "machine", "workorderNames": ["组装"]},
    {"id": "worker_electrical", "code": "worker_electrical", "name": "电控", "hostType": None,
     "productClass": "machine", "workorderNames": ["电控"]},
    {"id": "worker_packing", "code": "worker_packing", "name": "打包", "hostType": None,
     "productClass": "machine", "workorderNames": ["打包"]},
    # 主机类使用两个稳定的本地工序编码，分别绑定 Odoo 的组装/打包工单。
    # 编码保持不变，兼容已有本地工人绑定和历史报工记录。
    {"id": "pc_assembly_tape", "code": "pc_assembly_tape", "name": "组装", "hostType": None,
     "productClass": "host", "workorderNames": ["组装"], "odooWorkcenterId": 101, "odooWorkcenterCode": "pc_assembly_tape"},
    {"id": "pc_assembly_splitter", "code": "pc_assembly_splitter", "name": "打包", "hostType": None,
     "productClass": "host", "workorderNames": ["打包"], "odooWorkcenterId": 102, "odooWorkcenterCode": "pc_assembly_splitter"},
    {"id": "test_tape_operation", "code": "test_tape_operation", "name": "测试工序（编带）",
     "hostType": "tape", "odooWorkcenterId": 101,
     "odooWorkcenterCode": "pc_assembly_tape", "mockOnly": True},
    {"id": "test_splitter_operation", "code": "test_splitter_operation", "name": "测试工序（分光）",
     "hostType": "splitter", "odooWorkcenterId": 102,
     "odooWorkcenterCode": "pc_assembly_splitter", "mockOnly": True},
    {"id": "test_assembly_operation", "code": "test_assembly_operation", "name": "测试工序（组装）",
     "hostType": None, "workorderNames": ["组装"], "odooWorkcenterId": 103,
     "odooWorkcenterCode": "test_assembly", "mockOnly": True},
    {"id": "test_packing_operation", "code": "test_packing_operation", "name": "测试工序（打包）",
     "hostType": None, "workorderNames": ["打包"], "odooWorkcenterId": 104,
     "odooWorkcenterCode": "test_packing", "mockOnly": True},
]

VALID_OPERATIONS = {op["code"] for op in OPERATIONS}
OPERATION_MAP = {op["code"]: op for op in OPERATIONS}


WO_STATE_MAP = {
    "draft": "草稿", "pending": "待处理", "ready": "就绪",
    "waiting": "等待中", "progress": "生产中", "done": "完成",
    "cancel": "已取消", "to_close": "待关闭",
}
MO_STATE_MAP = {
    "draft": "草稿", "confirmed": "已确认", "progress": "生产中",
    "done": "完成", "cancel": "已取消", "to_close": "待关闭",
}

# Odoo 库位 ID
SRC_LOCATION_ID = 17      # WH/生产前（工人物料从这个库位扣）
DEST_LOCATION_ID = 15     # Virtual Locations/Production


def get_operations():
    """Return operations that are valid for the active Odoo mode."""
    mode = get_odoo_mode()
    ops = []
    for op in OPERATIONS:
        if mode == "real" and op.get("mockOnly"):
            continue
        o = dict(op)
        o["meta"] = {"mode": mode, "source": "odoo" if mode == "real" else "mock"}
        ops.append(o)
    return ops


def get_operations_for_worker(worker):
    """Return static operations plus trusted job-specific bindings."""
    static = {op["code"]: op for op in get_operations()}
    result = []
    custom_codes = set()
    role_bindings = []
    for role in worker.get("jobRoles", []) if worker else []:
        if not isinstance(role, dict) or role.get("enabled", True) is False:
            continue
        for operation in role.get("operations", []):
            if isinstance(operation, dict):
                enriched = dict(operation)
                enriched.setdefault("roleCode", role.get("code", ""))
                enriched.setdefault("roleName", role.get("name", ""))
                role_bindings.append(enriched)
    legacy_bindings = worker.get("operationBindings", []) if worker else []
    for binding in [*role_bindings, *legacy_bindings]:
        code = str(binding.get("code", ""))
        if not code:
            continue
        if code in static:
            merged = dict(static[code])
            merged.update({
                key: binding[key] for key in ("name", "processCode", "processName", "hostType", "productClass", "workorderNames", "requiresBom", "roleCode", "roleName", "woMatch")
                if key in binding and binding[key] not in (None, "", [])
            })
            static[code] = merged
            continue
        if code in custom_codes:
            continue
        result.append({
            "id": code,
            "code": code,
            "name": str(binding.get("name", code)),
            "processCode": str(binding.get("processCode", code)),
            "processName": str(binding.get("processName", binding.get("name", code))),
            "hostType": binding.get("hostType"),
            "productClass": binding.get("productClass"),
            "workorderNames": list(binding.get("workorderNames") or []),
            "requiresBom": bool(binding.get("requiresBom")),
            "roleCode": binding.get("roleCode", ""),
            "roleName": binding.get("roleName", ""),
            "woMatch": binding.get("woMatch") if isinstance(binding.get("woMatch"), dict) else {},
            "meta": {"mode": get_odoo_mode(), "source": "report_admin"},
        })
        custom_codes.add(code)
    return list(static.values()) + result


def operation_for_worker(worker, code):
    code = str(code or "")
    return next((op for op in get_operations_for_worker(worker) if op["code"] == code), None)


def role_for_worker_operation(worker, code):
    for role in worker.get("jobRoles", []) if worker else []:
        if not isinstance(role, dict) or role.get("enabled", True) is False:
            continue
        for operation in role.get("operations", []):
            if isinstance(operation, dict) and str(operation.get("code", "")) == str(code):
                return role, operation
    return None, None


def get_operation_map():
    return {op["code"]: op for op in get_operations()}


# ============================================================
# 认证校验
# ============================================================

# 内网IP白名单（当 API_KEY 未配置时使用）
_ALLOWED_IPS = {"127.0.0.1", "localhost", "::1"}  # 本机
_ALLOWED_PREFIXES = ("192.168.", "10.", "172.16.", "172.17.", "172.18.", "172.19.",
                     "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", "172.25.",
                     "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.")


def check_auth(handler):
    """API Key 或内网 IP 白名单认证"""
    if API_KEY:
        provided = handler.headers.get("X-API-Key", "")
        return provided == API_KEY
    # 无 API_KEY 时：只允许本机和内网
    client_ip = handler.client_address[0]
    if client_ip in _ALLOWED_IPS or any(client_ip.startswith(p) for p in _ALLOWED_PREFIXES):
        return True
    logger.warning(f"拒绝来自 {client_ip} 的 POST 请求 (无 API_KEY 且不在白名单)")
    return False


def check_report_admin_auth(handler):
    """Authenticate internal read and employee mirror calls from the admin service."""
    supplied = handler.headers.get("X-Internal-API-Key", "")
    return bool(REPORT_ADMIN_API_KEY and supplied and hmac.compare_digest(supplied, REPORT_ADMIN_API_KEY))


def is_report_admin_read_path(path):
    """Return whether a read-only endpoint may be accessed by the admin service."""
    return path in {"/api/reports", "/api/workers", "/api/workorders", "/api/order-summary"}


_worker_ids_lock = threading.Lock()

def get_valid_worker_ids():
    with _worker_ids_lock:
        workers = load_workers()
        return {w["id"] for w in workers}


def get_worker_by_id(worker_id):
    worker_id = str(worker_id or "")
    return next((w for w in load_workers() if str(w.get("id")) == worker_id), None)


def worker_allows_operation(worker_id, operation_code):
    worker = get_worker_by_id(worker_id)
    return bool(worker and operation_code in set(worker.get("operationCodes") or []))


def worker_required_product_class(worker):
    """Return the finished-product class allowed for this worker."""
    if not worker:
        return None
    if str(worker.get("id", "")) == "LOCAL_LWH":
        return "host"
    if str(worker.get("team", "")).strip().endswith("组装部"):
        return "host"
    if _effective_worker_source(worker.get("id"), worker.get("source")) in {"odoo", "report_admin"}:
        return "machine"
    return None


def panel_worker_allows_workorder(worker, workorder):
    """Apply the same operation and product-class rules before data is shown."""
    if not worker or not workorder:
        return False
    required_product_class = worker_required_product_class(worker)
    if (get_odoo_mode() == "real" and required_product_class
            and workorder.get("productClass") != required_product_class):
        return False
    return bool(panel_worker_matching_operation_codes(worker, workorder))


def panel_worker_matching_operation_codes(worker, workorder):
    """Return the worker's authorized operation codes that match a WO."""
    if not worker or not workorder:
        return []
    operation_map = {op["code"]: op for op in get_operations_for_worker(worker)}
    return [
        code for code in worker.get("operationCodes", [])
        if code in operation_map and operation_matches_workorder(operation_map[code], workorder)
    ]


def panel_accessible_workorders(worker):
    return [
        workorder for workorder in get_workorders_data()
        if panel_worker_allows_workorder(worker, workorder)
    ]


def panel_worker_can_access_workorder(worker, workorder_id):
    return any(
        str(workorder.get("workorderId")) == str(workorder_id)
        for workorder in panel_accessible_workorders(worker)
    )


_order_ids_cache = {"ids": set(), "ts": 0, "marker": 0}
_ORDER_CACHE_TTL = 60
_order_ids_lock = threading.Lock()

def get_valid_order_ids():
    now = time.time()
    marker = reset_marker_stamp()
    with _order_ids_lock:
        if (now - _order_ids_cache["ts"] < _ORDER_CACHE_TTL
                and _order_ids_cache["marker"] == marker
                and _order_ids_cache["ids"]):
            return _order_ids_cache["ids"]
    try:
        data = load_dashboard()
        ids = {row["order"] for row in data.get("deliveryRows", [])}
        with _order_ids_lock:
            _order_ids_cache["ids"] = ids
            _order_ids_cache["ts"] = time.time()
            _order_ids_cache["marker"] = reset_marker_stamp()
        return ids
    except Exception:
        with _order_ids_lock:
            return _order_ids_cache["ids"]


# ============================================================
# Odoo 辅助函数
# ============================================================

def rel_name(value, fallback=""):
    if isinstance(value, list) and len(value) > 1:
        return str(value[1])
    if value in (False, None, ""):
        return fallback
    return str(value)


def rel_id(value):
    if isinstance(value, list) and value:
        return value[0]
    return None


def clean_name(value):
    return re.sub(r"^\[[^\]]+\]\s*", "", rel_name(value)).strip()


def product_code(display, default=""):
    text = rel_name(display)
    match = re.match(r"^\[([^\]]+)\]", text)
    return match.group(1) if match else default


def workorder_host_type(product, workcenter=None):
    """Infer the host type from product/workcenter data.

    Product codes remain the strongest signal, while names provide a stable
    fallback when a new product code is introduced in Odoo.
    """
    code = product_code(product).upper()
    if code == "P04725":
        return "tape"
    if code == "P04726":
        return "splitter"
    searchable = f"{rel_name(product)} {rel_name(workcenter)}".casefold()
    if any(token in searchable for token in ("编带", "tape")):
        return "tape"
    if any(token in searchable for token in ("分光", "splitter")):
        return "splitter"
    return None


def workorder_product_class(product):
    """Classify finished products for worker access control."""
    if product_code(product).upper() in {"P04725", "P04726"}:
        return "host"
    name = clean_name(product).casefold()
    if any(token in name for token in ("主机", "host computer", "controller host")):
        return "host"
    if any(token in name for token in ("编带机", "分光机", "机器", "machine")):
        return "machine"
    return None


def operation_matches_workorder(operation, workorder):
    """Return whether an operation binding can use a specific Odoo WO."""
    if not operation or not workorder:
        return False
    # The management service owns the explicit, stable WO matching rules.
    # Evaluate them in descending specificity. Labels never authorize BOM use.
    match = operation.get("woMatch") if isinstance(operation.get("woMatch"), dict) else {}
    def _values(*keys):
        values = []
        for key in keys:
            value = match.get(key)
            if isinstance(value, list):
                values.extend(str(item) for item in value if item not in (None, ""))
            elif value not in (None, ""):
                values.append(str(value))
        return set(values)
    operation_ids = _values("operationId", "operationIds", "routingOperationId", "routingOperationIds")
    if operation_ids:
        return str(workorder.get("operationId", "")) in operation_ids
    product_ids = _values("productId", "productIds")
    product_classes = _values("productClass", "productClasses")
    workcenter_ids = _values("workcenterId", "workcenterIds")
    if product_ids or product_classes or workcenter_ids:
        return ((not product_ids or str(workorder.get("productId", "")) in product_ids)
                and (not product_classes or str(workorder.get("productClass", "")) in product_classes)
                and (not workcenter_ids or str(workorder.get("workcenterId", "")) in workcenter_ids))
    controlled_names = _values("workorderName", "workorderNames", "controlledWorkorderNames")
    if controlled_names:
        return _match_text(workorder.get("workorderName", "")) in {
            _match_text(value) for value in controlled_names
        }
    expected_host = operation.get("hostType")
    if expected_host and workorder.get("hostType") != expected_host:
        return False
    expected_product_class = operation.get("productClass")
    if expected_product_class and workorder.get("productClass") != expected_product_class:
        return False
    names = operation.get("workorderNames") or []
    workorder_name = workorder.get("workorderName", "")
    normalized_workorder_name = _match_text(workorder_name)
    normalized_names = {_match_text(name) for name in names if _match_text(name)}
    if names and normalized_workorder_name not in normalized_names:
        # Component assembly work orders are named by the routing operation,
        # while the employee binding is named by the material.  Odoo data can
        # use slightly different material wording (for example 杯/环), so
        # retain the exact WO match and then fall back to the selected MO BOM.
        custom_assembly = (not workorder.get("operationId")) and (bool(operation.get("requiresBom")) or str(
            operation.get("code", "")
        ).startswith("worker_assembly_custom_"))
        component_names = workorder.get("bomComponentNames") or []
        component_codes = workorder.get("bomComponentCodes") or []
        if not custom_assembly or not any(
            _custom_assembly_matches_workorder(
                name,
                workorder.get("workorderName", ""),
                [*component_names, *component_codes],
            )
            for name in names
        ):
            return False
    return True


def _match_text(value):
    """Normalize Odoo/worker material names for semantic comparisons."""
    text = clean_name(value)
    try:
        import unicodedata
        text = unicodedata.normalize("NFKC", text)
    except (TypeError, ValueError):
        pass
    text = text.casefold()
    return re.sub(r"[\s_\-./\\,，。:：()（）[\]【】]+", "", text)


def _similarity_ratio(left, right):
    """Return the same position-aligned ratio used by worker-report.js."""
    if not left or not right:
        return 1.0 if left == right else 0.0
    shared = sum(a == b for a, b in zip(left, right))
    return shared / max(len(left), len(right))


def _operation_material_variants(value):
    text = _match_text(value)
    variants = []
    if text:
        variants.append(text)
    # Routing suffixes belong to the operation, not the material name.
    for suffix in ("组装", "总装"):
        if text.endswith(suffix) and len(text) > len(suffix):
            text = text[: -len(suffix)]
            variants.append(text)
    if text.endswith("结构") and len(text) > 2:
        variants.append(text[:-2])
    return list(dict.fromkeys(item for item in variants if item))


def _material_matches_operation(operation_name, material_name):
    """Match a custom assembly name to a BOM component without hardcoding it."""
    operation_variants = _operation_material_variants(operation_name)
    material_variants = _operation_material_variants(material_name)
    for operation_text in operation_variants:
        for material_text in material_variants:
            if operation_text in material_text or material_text in operation_text:
                return True
            if min(len(operation_text), len(material_text)) < 4:
                continue
            if _similarity_ratio(operation_text, material_text) >= 0.72:
                return True
    return False


def _names_share_component_anchor(operation_name, workorder_name):
    """Require a meaningful routing-name link for BOM-based fallbacks."""
    # Compare the most specific, routing-suffix-free form only. Otherwise
    # every pair of Chinese component WOs would match on the shared “组装”.
    operation_variants = _operation_material_variants(operation_name)
    workorder_variants = _operation_material_variants(workorder_name)
    if not operation_variants or not workorder_variants:
        return False
    left = operation_variants[-1]
    right = workorder_variants[-1]
    # A BOM is shared by every component WO in the same MO. A generic common
    # word such as “电磁阀” must not expose sibling WOs, so the fallback uses
    # the leading component identifier only (for example NG废料环 -> NG吹气).
    prefix_length = 0
    for left_char, right_char in zip(left, right):
        if left_char != right_char:
            break
        prefix_length += 1
    return prefix_length >= 2


def _custom_assembly_matches_workorder(operation_name, workorder_name, components):
    """Match a component route without exposing sibling routes in the same MO."""
    return (
        _names_share_component_anchor(operation_name, workorder_name)
        and any(_material_matches_operation(operation_name, component) for component in components)
    )


def bracket_code(value):
    match = re.match(r"^\[([^\]]+)\]", value or "")
    return match.group(1) if match else value or "-"


def number(value):
    return float(value or 0)


def requires_all_route_steps(product):
    """Return whether a finished product is completed by every active WO.

    Hosts such as the tape host are machines too: producing one requires every
    active routing step to reach the same cumulative quantity.
    """
    return workorder_product_class(product) in {"machine", "host"}


def completed_machine_qty_for_reports(reports):
    """Calculate auditable completed machines from synchronized WO receipts.

    ``odoo_progress_qty`` is the cumulative WO quantity read back immediately
    after a successful panel report.  A finished unit requires a synchronized
    receipt for every non-cancelled WO in the manufacturing order; the minimum
    confirmed progress across those WOs is the finished output. This deliberately excludes raw
    Odoo cumulative changes that have no local audit receipt.
    """
    confirmed_qty_by_production = {}
    for report in reports:
        production_id = str(report.get("production_id") or report.get("productionId") or "")
        workorder_id = str(report.get("workorder_id") or report.get("workorderId") or "")
        sync_status = str(report.get("sync_status") or report.get("syncStatus") or "")
        progress_qty = report.get("odoo_progress_qty", report.get("odooProgressQty"))
        if (not production_id.isdigit() or not workorder_id.isdigit()
                or sync_status != "odoo_synced" or progress_qty is None):
            continue
        production_progress = confirmed_qty_by_production.setdefault(int(production_id), {})
        production_progress[int(workorder_id)] = max(
            production_progress.get(int(workorder_id), 0.0), number(progress_qty)
        )
    production_ids = set(confirmed_qty_by_production)
    if not production_ids:
        return 0
    client = get_odoo()
    mos = client.read(
        "mrp.production", list(production_ids), ["id", "product_id", "workorder_ids"]
    )
    completed = 0.0
    for mo in mos:
        if not requires_all_route_steps(mo.get("product_id")):
            continue
        # Query by production instead of relying on the routing relation. A
        # manually added WO can have no operation_id but must still block
        # finished-product output until that operation is reported.
        workorders = client.search_read(
            "mrp.workorder", [("production_id", "=", mo["id"])], ["id", "state"],
            limit=5000,
        )
        quantities = [
            confirmed_qty_by_production[mo["id"]].get(workorder["id"], 0.0)
            for workorder in workorders
            if workorder.get("state") != "cancel"
        ]
        if quantities and all(quantity > 0 for quantity in quantities):
            completed += min(quantities)
    return int(completed)


def completed_machine_qty_from_odoo_today(today):
    """Conservatively recover today's finished output from Odoo work orders.

    This is a read-only fallback for a restarted panel whose local daily audit
    rows are temporarily unavailable. A MO contributes only when every active
    routing WO was updated on the requested local date and has a positive
    completed quantity.
    """
    client = get_odoo()
    workorders = client.search_read(
        "mrp.workorder", [("state", "not in", ["cancel"])],
        ["id", "production_id", "qty_produced", "state", "write_date"],
        limit=5000,
    )
    by_production = {}
    for workorder in workorders:
        production_id = rel_id(workorder.get("production_id"))
        if production_id:
            by_production.setdefault(production_id, []).append(workorder)

    completed = 0.0
    for production_id, route in by_production.items():
        mo_rows = client.read("mrp.production", [production_id], ["product_id"])
        if not mo_rows or not requires_all_route_steps(mo_rows[0].get("product_id")):
            continue
        if not route or not all(
            str(workorder.get("write_date") or "")[:10] == today
            and number(workorder.get("qty_produced")) > 0
            for workorder in route
        ):
            continue
        completed += min(number(workorder.get("qty_produced")) for workorder in route)
    return int(completed)


def odoo_today_progress_snapshots(local_reports):
    """Return read-only panel rows for today's Odoo work-order changes.

    Odoo keeps a cumulative ``qty_produced`` on each work order, not the
    original quantity of a missing panel request.  The rows returned here are
    therefore display-only snapshots: they are not inserted into SQLite and
    must not be used to calculate submitted quantity or finished output.
    """
    local_now = datetime.now(LOCAL_TZ)
    today = local_now.strftime("%Y-%m-%d")
    local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    utc_start = local_start.astimezone(timezone.utc)
    utc_end = utc_start + timedelta(days=1)
    existing_workorders = {
        str(report.get("workorder_id") or report.get("workorderId") or "")
        for report in local_reports
        if str(report.get("date") or "") == today
    }
    client = get_odoo()
    rows = client.search_read(
        "mrp.workorder",
        [("write_date", ">=", utc_start.strftime("%Y-%m-%d %H:%M:%S")),
         ("write_date", "<", utc_end.strftime("%Y-%m-%d %H:%M:%S"))],
        ["id", "name", "production_id", "product_id", "qty_produced", "write_date"],
        limit=500,
        order="write_date desc",
    )
    snapshots = []
    for workorder in rows:
        workorder_id = str(workorder.get("id") or "")
        production_id = rel_id(workorder.get("production_id"))
        if not workorder_id or not production_id or workorder_id in existing_workorders:
            continue
        write_date = str(workorder.get("write_date") or "")
        try:
            changed_at = datetime.strptime(
                write_date, "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=timezone.utc).astimezone(LOCAL_TZ)
        except ValueError:
            continue
        snapshots.append({
            "id": f"odoo-progress-{workorder_id}-{write_date}",
            "worker_id": "",
            "worker_name": "Odoo 工单进度",
            "worker_team": "",
            "order_id": f"odoo-progress-{workorder_id}",
            "order_customer": "",
            "order_product": clean_name(workorder.get("product_id")),
            "operation": f"odoo_workorder_{workorder_id}",
            "operation_label": str(workorder.get("name") or "工单进度"),
            "qty": 0,
            "qualified": 0,
            "hours": 0,
            "remark": "仅展示 Odoo 当日工单进度，未补造本地报工记录",
            "date": today,
            "time": changed_at.strftime("%H:%M"),
            "timestamp": int(changed_at.timestamp() * 1000),
            "production_id": str(production_id),
            "workorder_id": workorder_id,
            "odoo_employee_id": 0,
            "idempotency_key": "",
            "odoo_report_id": workorder_id,
            "odoo_stock_move_ids": "[]",
            "sync_status": "odoo_progress_snapshot",
            "material_sync_status": "unknown",
            "odoo_progress_qty": number(workorder.get("qty_produced")),
            "error_message": "仅展示 Odoo 当日工单进度，未补造本地报工记录",
            "odoo_display_only": True,
        })
    return snapshots


def normalize_machine_bom_materials(submitted_items, expected_items):
    """Validate component identity while preserving confirmed actual usage."""
    if not isinstance(submitted_items, list) or not submitted_items:
        raise ValueError("机器组装工序必须确认物料清单")

    unmatched = list(submitted_items)
    normalized = []
    for expected in expected_items:
        expected_product_id = int(expected.get("productId") or 0)
        expected_bom_line_id = int(expected.get("bomLineId") or 0)
        match_index = next((
            index for index, submitted in enumerate(unmatched)
            if int(submitted.get("productId") or 0) == expected_product_id
            and int(submitted.get("bomLineId") or 0) == expected_bom_line_id
        ), None)
        if match_index is None:
            raise ValueError("提交的物料与该制造订单的 Odoo BOM 不一致")

        submitted = unmatched.pop(match_index)
        actual_qty = submitted.get("actualQty")
        if isinstance(actual_qty, bool) or not isinstance(actual_qty, (int, float)):
            raise ValueError(f"物料 {expected.get('defaultCode', '')} 实际使用数量必须为正数")
        actual_qty = float(actual_qty)
        if not math.isfinite(actual_qty) or actual_qty <= 0:
            raise ValueError(f"物料 {expected.get('defaultCode', '')} 实际使用数量必须为正数")

        normalized.append({
            "productId": expected_product_id,
            "bomLineId": expected_bom_line_id,
            "defaultCode": expected.get("defaultCode", ""),
            "actualQty": actual_qty,
            "uomId": expected.get("uomId", 1),
        })

    if unmatched:
        raise ValueError("提交的物料与该制造订单的 Odoo BOM 不一致")
    return normalized


def qty_text(value):
    value = number(value)
    return str(int(value)) if value.is_integer() else f"{value:g}"


def parse_dt(value):
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def local_time(value, fmt="%m-%d %H:%M"):
    dt = parse_dt(value)
    return dt.astimezone(LOCAL_TZ).strftime(fmt) if dt else "-"


def local_dt(value, fmt="%m-%d %H:%M"):
    return value.astimezone(LOCAL_TZ).strftime(fmt) if value else "-"


def max_dt(*values):
    dates = [dt for dt in (parse_dt(value) for value in values) if dt]
    return max(dates) if dates else None


def order_number(value):
    match = re.search(r"(\d+)$", value or "")
    return int(match.group(1)) if match else 0


def due_state(value, remaining):
    if remaining <= 0:
        return "已完成"
    dt = parse_dt(value)
    if not dt:
        return "待交付"
    today = datetime.now(LOCAL_TZ).date()
    day = dt.astimezone(LOCAL_TZ).date()
    if day < today:
        return "已逾期"
    if day == today:
        return "今日交付"
    return "待交付"


def build_stages(qty, delivered, remaining, need_qty, supplier, mrp, delivery_status):
    mrp_state = (mrp or {}).get("state") or ""
    mrp_labels = {
        "draft": "草稿", "confirmed": "待生产", "progress": "生产中",
        "to_close": "待关闭", "done": "完成", "cancel": "取消",
    }
    if remaining <= 0:
        return [
            ["销售订单", "完成", "done"],
            ["库存预警", "完成", "done"],
            ["采购下单", "完成", "done"],
            ["生产规划", "完成", "done"],
            ["交付", "完成", "done"],
        ]
    stock_stage = ["库存预警", f"缺{qty_text(need_qty)}", "warning"] if need_qty > 0 else ["库存预警", "库存OK", "done"]
    if need_qty > 0:
        purchase_stage = ["采购下单", "待下单", "running"] if supplier else ["采购下单", "待配供应商", "warning"]
    else:
        purchase_stage = ["采购下单", "无需采购", "done"]
    if mrp:
        production_stage = [
            "生产规划", mrp_labels.get(mrp_state, mrp_state or "已录入"),
            "done" if mrp_state in ("done", "to_close") else "running" if mrp_state == "progress" else "warning",
        ]
    elif need_qty > 0:
        production_stage = ["生产规划", "待规划", "pending"]
    else:
        production_stage = ["生产规划", "待录入", "pending"]
    if delivered > 0:
        delivery_stage = ["交付", f"已交{qty_text(delivered)}", "running"]
    elif delivery_status == "已逾期":
        delivery_stage = ["交付", "已逾期", "danger"]
    else:
        delivery_stage = ["交付", "待交付", "pending"]
    return [
        ["销售订单", "完成", "done"],
        stock_stage, purchase_stage, production_stage, delivery_stage,
    ]


# ============================================================
# Dashboard 数据加载
# ============================================================
_DASH_CACHE = {"data": None, "ts": 0, "marker": 0}
_DASH_CACHE_LOCK = threading.Lock()
_DASH_CACHE_TTL = 120  # 2分钟缓存，准实时


def load_dashboard():
    """加载 Dashboard 数据（120 秒缓存，减少 Odoo 重复查询）。"""
    now = time.time()
    marker = reset_marker_stamp()
    with _DASH_CACHE_LOCK:
        if (_DASH_CACHE["data"] is not None
                and _DASH_CACHE["marker"] == marker
                and (now - _DASH_CACHE["ts"]) < _DASH_CACHE_TTL):
            logger.info("Dashboard缓存命中")
            return _DASH_CACHE["data"]

    client = get_odoo()
    recent_start = (datetime.now(LOCAL_TZ) - timedelta(days=7)).astimezone(timezone.utc)
    recent_start_text = recent_start.strftime("%Y-%m-%d %H:%M:%S")

    op_fields = [
        "name", "product_id", "spec_info", "qty_on_hand", "qty_forecast",
        "qty_to_order", "product_uom_name", "product_supplier_id", "write_date",
    ]
    try:
        orderpoint_rows = client.search_read(
            "stock.warehouse.orderpoint", [["qty_to_order", ">", 0]],
            op_fields, limit=80, order="write_date desc",
        )
    except Exception:
        orderpoint_rows = []
    ops_by_product_id = {rel_id(row.get("product_id")): row for row in orderpoint_rows}
    ops_by_code = {product_code(row.get("product_id")): row for row in orderpoint_rows}

    order_fields = [
        "name", "partner_id", "user_id", "state", "date_order",
        "expected_date", "commitment_date", "delivery_status", "amount_total", "write_date",
    ]
    line_fields = [
        "order_id", "product_id", "default_code", "spec_info", "name",
        "product_uom_qty", "qty_delivered", "qty_to_deliver", "product_uom",
        "state", "scheduled_date", "create_date", "write_date",
    ]

    try:
        recent_orders = client.search_read(
            "sale.order", [["state", "=", "sale"], ["write_date", ">=", recent_start_text]],
            order_fields, limit=160, order="write_date desc",
        )
    except Exception:
        recent_orders = []
    recent_order_ids = [row["id"] for row in recent_orders]
    try:
        recent_line_rows = client.search_read(
            "sale.order.line", [["state", "=", "sale"], ["write_date", ">=", recent_start_text]],
            line_fields, limit=200, order="write_date desc",
        )
    except Exception:
        recent_line_rows = []
    linked_line_rows = []
    if recent_order_ids:
        try:
            linked_line_rows = client.search_read(
                "sale.order.line", [["state", "=", "sale"], ["order_id", "in", recent_order_ids]],
                line_fields, limit=200, order="write_date desc",
            )
        except Exception:
            linked_line_rows = []
    sale_line_map = {row["id"]: row for row in recent_line_rows}
    sale_line_map.update({row["id"]: row for row in linked_line_rows})
    sale_lines = list(sale_line_map.values())
    order_ids = sorted({rel_id(line.get("order_id")) for line in sale_lines if rel_id(line.get("order_id"))})
    orders = {row["id"]: row for row in recent_orders}
    missing_order_ids = [oid for oid in order_ids if oid not in orders]
    try:
        orders.update({row["id"]: row for row in client.read("sale.order", missing_order_ids, order_fields)})
    except Exception:
        pass

    mrp_rows = []
    try:
        mrp_rows = client.search_read(
            "mrp.production", [["state", "not in", ["done", "cancel"]]],
            ["name", "origin", "product_id", "product_qty", "qty_produced",
             "state", "reservation_state", "date_deadline", "write_date"],
            limit=120, order="write_date desc",
        )
    except Exception:
        mrp_rows = []
    mrp_by_product_id = {rel_id(row.get("product_id")): row for row in mrp_rows}
    mrp_by_code = {product_code(row.get("product_id")): row for row in mrp_rows}

    delivery_rows = []
    total_qty = delivered_qty = remaining_qty = 0.0
    for line in sale_lines:
        qty = number(line.get("product_uom_qty"))
        delivered = min(max(number(line.get("qty_delivered")), 0), qty)
        remaining = min(max(number(line.get("qty_to_deliver")), qty - delivered, 0), qty)
        if remaining <= 0:
            continue
        order = orders.get(rel_id(line.get("order_id")), {})
        code = line.get("default_code") or product_code(line.get("product_id"))
        op = ops_by_product_id.get(rel_id(line.get("product_id"))) or ops_by_code.get(code) or {}
        need_qty = number(op.get("qty_to_order"))
        product = clean_name(line.get("product_id"))
        spec = line.get("spec_info") or op.get("spec_info") or "-"
        due = order.get("commitment_date")
        display_due = due or order.get("date_order")
        status = due_state(due, remaining)
        mrp = mrp_by_product_id.get(rel_id(line.get("product_id"))) or mrp_by_code.get(code)
        if remaining > 0 and need_qty > 0:
            status = "待采购" if not op.get("product_supplier_id") else "待下单"
        elif remaining > 0 and mrp:
            status = "生产中" if mrp.get("state") == "progress" else "已规划"
        remark = "补货缺口 " + qty_text(need_qty) if need_qty > 0 else ("已交付" if remaining <= 0 else "Odoo待交付")
        updated_at = max_dt(
            line.get("write_date"), order.get("write_date"),
            op.get("write_date"), (mrp or {}).get("write_date"),
        )
        total_qty += qty
        delivered_qty += delivered
        remaining_qty += remaining
        delivery_rows.append({
            "customer": rel_name(order.get("partner_id"), "-"),
            "customerCode": bracket_code(rel_name(order.get("partner_id"), "-")),
            "order": rel_name(line.get("order_id"), "-"),
            "machine": product, "code": code, "spec": spec,
            "qty": qty_text(qty), "uom": rel_name(line.get("product_uom"), ""),
            "remark": remark, "splitter": "-", "delivery": status,
            "owner": rel_name(order.get("user_id"), "-"),
            "date": local_time(display_due), "updated": local_dt(updated_at),
            "remaining": qty_text(remaining),
            "priority": "danger" if status == "已逾期" else "warning" if status in ("待采购", "待下单", "已规划") else "running",
            "stages": build_stages(qty, delivered, remaining, need_qty, op.get("product_supplier_id"), mrp, status),
            "_sort": order_number(rel_name(line.get("order_id"), "")),
            "_updated_ts": updated_at.timestamp() if updated_at else 0,
        })
    delivery_rows.sort(key=lambda row: (row.get("_updated_ts", 0), row.get("_sort", 0)), reverse=True)

    replenish_rows = []
    recent_orderpoint_rows = [r for r in orderpoint_rows if (parse_dt(r.get("write_date")) or datetime.min.replace(tzinfo=timezone.utc)) >= recent_start]
    for row in recent_orderpoint_rows:
        replenish_rows.append({
            "product": clean_name(row.get("product_id")),
            "code": product_code(row.get("product_id")),
            "spec": row.get("spec_info") or "-",
            "onHand": qty_text(row.get("qty_on_hand")),
            "forecast": qty_text(row.get("qty_forecast")),
            "toOrder": qty_text(row.get("qty_to_order")),
            "uom": row.get("product_uom_name") or "",
            "supplier": rel_name(row.get("product_supplier_id"), "待配供应商"),
            "updated": local_time(row.get("write_date")),
        })
    pending_rows = delivery_rows
    pending_order_count = len({row["order"] for row in pending_rows})
    replenish_qty = sum(number(r.get("qty_to_order")) for r in recent_orderpoint_rows)
    supplier_missing = sum(1 for r in recent_orderpoint_rows if number(r.get("qty_to_order")) > 0 and not r.get("product_supplier_id"))
    active_mrp_count = 0
    try:
        active_mrp_count = client.call("mrp.production", "search_count", [[["state", "not in", ["done", "cancel"]]]], {})
    except Exception:
        active_mrp_count = 0

    mode = get_odoo_mode()
    kpis = [
        ["最近待处理", str(pending_order_count), "单", "最近7天更新", "#3b82f6"],
        ["待处理行", str(len(pending_rows)), "行", "交付后自动消失", "#22b8cf"],
        ["待交付数量", qty_text(remaining_qty), "台/套", "qty_to_deliver 汇总", "#20b26b"],
        ["补货缺口", qty_text(replenish_qty), "台", f"近7天补货 {len(recent_orderpoint_rows)} 条", "#f07a35"],
        ["待配供应商", str(supplier_missing), "条", "补货规则未配置供应商", "#eab842"],
        ["数据来源", "Odoo" if mode == "real" else "模拟", "", f"模式: {mode}", "#eab842"],
    ]
    alerts = []
    for row in delivery_rows[:3]:
        alerts.append([row["delivery"], row["order"],
                       f"{row['customer']} · {row['machine']} {row['spec']} · 数量 {row['qty']}，{row['remark']}，更新 {row['updated']}",
                       row["owner"]])
    if not alerts:
        alerts.append(["提示", "暂无待处理", "最近7天暂无需要展示的在产订单。", "-"])
    latest_orders = []
    seen = set()
    for row in delivery_rows:
        if row["order"] in seen:
            continue
        seen.add(row["order"])
        latest_orders.append([row["order"], row["customer"], row["updated"], row["delivery"]])
        if len(latest_orders) >= 6:
            break
    result = {
        "kpis": kpis, "deliveryRows": delivery_rows[:12],
        "replenishments": replenish_rows[:6], "latestOrders": latest_orders,
        "alerts": alerts,
        "meta": {
            "source": "odoo" if mode == "real" else "mock",
            "mode": mode,
            "db": ODOO_DB, "user": ODOO_USER,
            "updatedAt": datetime.now(LOCAL_TZ).isoformat(timespec="seconds"),
            "accuracyNote": "客户、订单、产品、规格、数量、待交付、补货缺口、供应商配置均来自 Odoo 原字段。",
            "range": "最近7天更新",
            "progressNote": "核心区只展示最近7天更新且仍待交付的订单行。",
        },
    }
    # Do not let a request that started before reset repopulate the cache with
    # stale Odoo data after the reset marker changes.
    with _DASH_CACHE_LOCK:
        if reset_marker_stamp() == marker:
            _DASH_CACHE["data"] = result
            _DASH_CACHE["ts"] = time.time()
            _DASH_CACHE["marker"] = marker
    return result


# ============================================================
# 新增: BOM 数据查询
# ============================================================

# BOM 定义（基于真实 Odoo 调查 + Excel）
TAPE_BOM_CODES = ["P04725", "P05346", "P05347", "P05350", "P05351", "P05352", "P05353"]
SPLITTER_BOM_CODES = ["P04726", "P05346", "P05347", "P05348", "P05351", "P05352", "P05353"]

# 真实 Odoo product ID 映射
ODOO_PRODUCT_IDS = {
    "P04725": 11632, "P05346": 12253, "P05347": 12254, "P05350": 12257,
    "P05348": 12255, "P05351": 12258, "P05352": 12259, "P05353": 12260,
    "P04726": 11633,
}

# 真实 Odoo product.template ID 映射
ODOO_TMPL_IDS = {
    "P04725": 12977, "P05346": 13001, "P05347": 13002, "P05350": 13005,
    "P05348": 13003, "P05351": 13006, "P05352": 13007, "P05353": 13008,
    "P04726": 12978,
}

# BOM Line ID 映射（Mock 模式下使用）
MOCK_BOM_LINE_IDS = {
    "tape": {"P04725": 3001, "P05346": 3002, "P05347": 3003, "P05350": 3004,
             "P05351": 3005, "P05352": 3006, "P05353": 3007},
    "splitter": {"P04726": 3008, "P05346": 3009, "P05347": 3010, "P05348": 3011,
                 "P05351": 3012, "P05352": 3013, "P05353": 3014},
}

# Excel BOM 数据（来自主机BOM物料清单登记表.xlsx）
EXCEL_BOM = {
    "tape": [
        {"seq": 1, "defaultCode": "P04725", "name": "编带机箱", "spec": "黑色:4U300",
         "uom": "pcs", "qty": 1, "category": "主机配件", "brand": "淘宝"},
        {"seq": 2, "defaultCode": "P05346", "name": "cpu", "spec": "I3-3220",
         "uom": "pcs", "qty": 1, "category": "主机配件", "brand": "淘宝"},
        {"seq": 3, "defaultCode": "P05347", "name": "内存条", "spec": "DDR3-4G",
         "uom": "pcs", "qty": 1, "category": "主机配件", "brand": "淘宝"},
        {"seq": 4, "defaultCode": "P05350", "name": "硬盘", "spec": "SSD-128G",
         "uom": "pcs", "qty": 1, "category": "主机配件", "brand": "淘宝"},
        {"seq": 5, "defaultCode": "P05351", "name": "显卡", "spec": "G210",
         "uom": "pcs", "qty": 1, "category": "主机配件", "brand": "淘宝"},
        {"seq": 6, "defaultCode": "P05352", "name": "机箱电源", "spec": "ATX-400W",
         "uom": "pcs", "qty": 1, "category": "主机配件", "brand": "淘宝"},
        {"seq": 7, "defaultCode": "P05353", "name": "机箱风扇", "spec": "",
         "uom": "pcs", "qty": 1, "category": "主机配件", "brand": "淘宝"},
    ],
    "splitter": [
        {"seq": 1, "defaultCode": "P04726", "name": "分光机箱", "spec": "4U-610H",
         "uom": "pcs", "qty": 1, "category": "主机配件", "brand": "淘宝"},
        {"seq": 2, "defaultCode": "P05346", "name": "cpu", "spec": "I3-3220",
         "uom": "pcs", "qty": 1, "category": "主机配件", "brand": "淘宝"},
        {"seq": 3, "defaultCode": "P05347", "name": "内存条", "spec": "DDR3-4G",
         "uom": "pcs", "qty": 1, "category": "主机配件", "brand": "淘宝"},
        {"seq": 4, "defaultCode": "P05348", "name": "硬盘", "spec": "SSD-64G",
         "uom": "pcs", "qty": 1, "category": "主机配件", "brand": "淘宝"},
        {"seq": 5, "defaultCode": "P05351", "name": "显卡", "spec": "G210",
         "uom": "pcs", "qty": 1, "category": "主机配件", "brand": "淘宝"},
        {"seq": 6, "defaultCode": "P05352", "name": "机箱电源", "spec": "ATX-400W",
         "uom": "pcs", "qty": 1, "category": "主机配件", "brand": "淘宝"},
        {"seq": 7, "defaultCode": "P05353", "name": "机箱风���", "spec": "",
         "uom": "pcs", "qty": 1, "category": "主机配件", "brand": "淘宝"},
    ],
}


# ============================================================
# BOM 数据缓存（线程安全）
# ============================================================
_BOM_CACHE = {"data": None, "ts": 0, "key": None, "marker": 0}
_BOM_CACHE_LOCK = threading.Lock()
_BOM_CACHE_TTL = 30      # 30秒缓存，确保 Odoo BOM 修改后能快速生效


def get_bom_data(host_type):
    """获取 BOM 数据（缓存 + 线程安全）"""
    if host_type not in ("tape", "splitter"):
        return []

    now = time.time()
    marker = reset_marker_stamp()
    cache_key = f"{get_odoo_mode()}:{host_type}"
    with _BOM_CACHE_LOCK:
        if (_BOM_CACHE["key"] == cache_key
                and _BOM_CACHE["data"] is not None
                and _BOM_CACHE["marker"] == marker
                and (now - _BOM_CACHE["ts"]) < _BOM_CACHE_TTL):
            logger.info(f"BOM缓存命中 [{host_type}]")
            return _BOM_CACHE["data"]

    codes = TAPE_BOM_CODES if host_type == "tape" else SPLITTER_BOM_CODES
    excel_items = EXCEL_BOM.get(host_type, [])
    bom_line_ids = MOCK_BOM_LINE_IDS.get(host_type, {})
    mode = get_odoo_mode()

    client = get_odoo() if mode == "real" else None

    # 真模式：直接从 Odoo mrp.bom 拉最新的 BOM lines（按成品 product code 找）
    # 编带机箱 → 找 product.code=P04725 的 BOM；分光机箱 → P04726
    bom_items_data = []  # [{product_code, qty, sequence}]
    if mode == "real" and client:
        try:
            target_product_code = "P04725" if host_type == "tape" else "P04726"
            # 找到对应的 product.template
            tmpl = models_query_tmpl_by_code(client, target_product_code)
            if tmpl:
                # 找这个产品的最新 BOM（按 code/name 排序，取第一条）
                bom_ids = client.call("mrp.bom", "search", [
                    [("product_tmpl_id", "=", tmpl), ("type", "=", "normal")]
                ], {"order": "id desc", "limit": 1})
                if bom_ids:
                    lines = client.call("mrp.bom.line", "search_read",
                        [[("bom_id", "=", bom_ids[0])]],
                        {"fields": ["id", "product_id", "product_qty", "sequence"]})
                    for ln in lines:
                        pid = rel_id(ln.get("product_id"))
                        bom_items_data.append({
                            "bom_line_id": ln["id"],
                            "product_id": pid,
                            "product_qty": float(ln.get("product_qty", 1)),
                            "sequence": ln.get("sequence", 0),
                        })
                    logger.info(f"BOM[{bom_ids[0]}] 从 Odoo 拉到 {len(bom_items_data)} 条 lines")
                else:
                    logger.warning(f"Odoo 中找不到 product={target_product_code} 的 BOM")
            else:
                logger.warning(f"Odoo 中找不到 product.code={target_product_code}")
        except Exception as e:
            logger.warning(f"查询 mrp.bom 失败: {e}")

    # 降级：Odoo 查不到时回退到 EXCEL_BOM 硬编码
    if not bom_items_data:
        for i, code in enumerate(codes):
            excel = excel_items[i] if i < len(excel_items) else {}
            pid = 0
            for _pid, pdata in ({} if not mode == "real" or not client else
                                  {pid: pdata for pid, pdata in {}}.items()):
                pass
            bom_items_data.append({
                "bom_line_id": bom_line_ids.get(code, 0),
                "product_id": 0,
                "product_code": code,
                "product_qty": excel.get("qty", 1),
                "sequence": i + 1,
            })
        logger.info(f"降级使用 EXCEL_BOM 硬编码 {len(bom_items_data)} 条")

    # 一次性查出所有 product.product
    products_by_id = {}
    if mode == "real" and client:
        # 从 bom_items_data 提取所有 product_id
        all_pids = [d["product_id"] for d in bom_items_data if d.get("product_id")]
        if all_pids:
            try:
                rows = client.search_read(
                    "product.product",
                    [("id", "in", list(set(all_pids)))],
                    ["id", "default_code", "name", "product_tmpl_id", "categ_id", "uom_id", "seller_ids"],
                    limit=50
                )
                for r in rows:
                    tmpl = r.get("product_tmpl_id")
                    tmpl_id = tmpl[0] if isinstance(tmpl, (list, tuple)) else tmpl
                    products_by_id[r["id"]] = {
                        "code": r.get("default_code", ""),
                        "name": r.get("name", ""),
                        "categ": r.get("categ_id", ""),
                        "uom": r.get("uom_id", ""),
                        "tmpl_id": tmpl_id,
                        "seller_ids": r.get("seller_ids", []) or [],
                    }
            except Exception as e:
                logger.warning(f"获取 product.product 失败: {e}")

    # 一次性查出所有 product.template（仅用于规格）
    templates_by_id = {}
    tmpl_ids = {p["tmpl_id"] for p in products_by_id.values() if p.get("tmpl_id")}
    if mode == "real" and client and tmpl_ids:
        try:
            trows = client.read(
                "product.template",
                list(tmpl_ids),
                ["id", "spec_info"]
            )
            for tr in trows:
                templates_by_id[tr["id"]] = {
                    "spec": tr.get("spec_info", "") or "",
                }
        except Exception as e:
            logger.warning(f"获取 product.template 失败: {e}")

    # 一次性查出所有供应商（product.supplierinfo），通过 partner_id 取供应商名称
    supplier_name_by_pid = {}  # {product.product.id: 供应商名称}
    all_seller_ids = []
    for pdata in products_by_id.values():
        for sid in pdata.get("seller_ids", []):
            if sid not in all_seller_ids:
                all_seller_ids.append(sid)
    if mode == "real" and client and all_seller_ids:
        try:
            srows = client.read(
                "product.supplierinfo",
                all_seller_ids,
                ["id", "product_id", "partner_id"]
            )
            # 建立 supplierinfo_id -> partner_name 映射
            sinfo_to_partner = {}
            for sr in srows:
                p = sr.get("partner_id", "")
                if isinstance(p, (list, tuple)) and len(p) > 1:
                    sinfo_to_partner[sr["id"]] = p[1]
            # 反向映射：product.product.id -> 供应商名称
            for pdata in products_by_id.values():
                for sid in pdata.get("seller_ids", []):
                    if sid in sinfo_to_partner and pdata["code"]:
                        supplier_name_by_pid[pdata["code"]] = sinfo_to_partner[sid]
                        break
        except Exception as e:
            logger.warning(f"获取 product.supplierinfo 失败: {e}")

    # 一次性批量查询所有物料的库存
    stock_by_pid = {}  # {product_id: available_quantity}
    if mode == "real" and client and products_by_id:
        try:
            product_ids = list(products_by_id.keys())
            stock_rows = client.search_read(
                "stock.quant",
                [("product_id", "in", product_ids), ("quantity", ">", 0)],
                ["product_id", "available_quantity"], 50
            )
            for s in stock_rows:
                pid = rel_id(s.get("product_id"))
                avail = number(s.get("available_quantity", 0))
                stock_by_pid[pid] = stock_by_pid.get(pid, 0) + avail
        except Exception as e:
            logger.warning(f"批量查询 stock.quant 失败: {e}")

    items = []
    # 按 bom_items_data 顺序构建 items（每个 line 对应一个物料）
    # code -> excel 数据映射（兼容降级场景）
    code_to_excel = {}
    for i, code in enumerate(codes):
        if i < len(excel_items):
            code_to_excel[code] = excel_items[i]

    for bom_item in bom_items_data:
        pid = bom_item.get("product_id", 0)
        bom_line_id = bom_item.get("bom_line_id", 0)
        odoo_qty = bom_item.get("product_qty", 1)

        # 取这个 product 的代码
        pdata = products_by_id.get(pid, {}) if pid else {}
        code = bom_item.get("product_code") or pdata.get("code", "")
        excel = code_to_excel.get(code, {})

        # 默认值（Odoo 读不到时回退到 Excel）
        product_name = pdata.get("name", "") or excel.get("name", "")
        spec = ""
        tmpl_id = pdata.get("tmpl_id", 0) or 0
        if tmpl_id and tmpl_id in templates_by_id:
            spec = templates_by_id[tmpl_id].get("spec", "") or excel.get("spec", "")
        else:
            spec = excel.get("spec", "")
        category_name = excel.get("category", "主机配件")
        brand_name = supplier_name_by_pid.get(code, excel.get("brand", ""))

        # 清理供应商名称
        if brand_name and brand_name.startswith("["):
            m = re.match(r"^\[[^\]]+\]\s*(.+)$", brand_name)
            if m:
                brand_full = m.group(1).strip()
                if "淘宝" in brand_full:
                    brand_name = "淘宝"
                else:
                    brand_name = brand_full

        available_qty = stock_by_pid.get(pid, 0) if pid else 0

        item = {
            "bomLineId": bom_line_id,
            "productId": pid,
            "productTemplateId": tmpl_id,
            "defaultCode": code,
            "name": product_name,
            "specification": spec,
            "uomId": 1,
            "uomName": "pcs",
            "bomQty": odoo_qty,
            "categoryName": category_name,
            "brandSupplierName": brand_name,
            "availableQty": available_qty,
            "selected": True,
            "actualQty": odoo_qty,
            "meta": {"mode": mode, "source": "odoo" if mode == "real" else "mock"},
        }
        items.append(item)

    # 写入缓存（线程安全）
    with _BOM_CACHE_LOCK:
        if reset_marker_stamp() == marker:
            _BOM_CACHE["data"] = items
            _BOM_CACHE["ts"] = time.time()
            _BOM_CACHE["key"] = cache_key
            _BOM_CACHE["marker"] = marker
    return items


def get_workorder_bom_data(workorder_id, operation=None):
    """Read the selected work order's own Odoo BOM and source stock.

    Custom component-assembly operations only receive the BOM lines assigned
    to the selected routing operation. This keeps both the confirmation list
    and the one-time inventory deduction scoped to that component.
    """
    if not workorder_id:
        raise ValueError("缺少工单 ID")
    client = get_odoo()
    wo_fields = ["id", "production_id", "product_id", "name"]
    try:
        wo_rows = client.read("mrp.workorder", [int(workorder_id)], wo_fields + ["operation_id"])
    except Exception:
        # Keep existing deployments working when their Odoo customization does
        # not expose operation_id. Custom assembly then fails closed below
        # instead of accidentally displaying the whole BOM.
        wo_rows = client.read("mrp.workorder", [int(workorder_id)], wo_fields)
    if not wo_rows:
        raise ValueError(f"工单 #{workorder_id} 不存在")
    wo = wo_rows[0]
    production_id = rel_id(wo.get("production_id"))
    if not production_id:
        raise ValueError("工单没有关联制造订单")
    mo_rows = client.read(
        "mrp.production", [production_id],
        ["id", "name", "product_id", "product_qty", "bom_id", "location_src_id"],
    )
    if not mo_rows:
        raise ValueError(f"制造订单 #{production_id} 不存在")
    mo = mo_rows[0]
    bom_id = rel_id(mo.get("bom_id"))
    if not bom_id:
        raise ValueError(f"制造订单 {mo.get('name', production_id)} 没有关联 BOM")
    source_location_id = rel_id(mo.get("location_src_id"))
    if source_location_id != SRC_LOCATION_ID:
        raise ValueError(
            f"制造订单原料库位不是 WH/生产前（实际库位 ID: {source_location_id}）"
        )

    line_fields = ["id", "product_id", "product_qty", "product_uom_id", "sequence"]
    try:
        lines = client.search_read(
            "mrp.bom.line", [("bom_id", "=", bom_id)], line_fields + ["operation_id"],
            limit=500, order="sequence asc, id asc",
        )
    except Exception:
        lines = client.search_read(
            "mrp.bom.line", [("bom_id", "=", bom_id)], line_fields,
            limit=500, order="sequence asc, id asc",
        )

    operation_requires_bom = _operation_requires_workorder_bom(operation)
    operation_filter_id = rel_id(wo.get("operation_id")) if operation_requires_bom else None
    operation_name_fallback = False
    if operation_requires_bom and operation_filter_id:
        operation_lines = [
            line for line in lines
            if rel_id(line.get("operation_id")) == operation_filter_id
        ]
        if operation_lines:
            lines = operation_lines
        else:
            # BOM lines in Odoo may not carry the selected WO operation_id.
            # Keep the complete MO BOM for semantic product-name matching.
            operation_name_fallback = True
    elif operation_requires_bom:
        operation_name_fallback = True
    product_ids = sorted({rel_id(line.get("product_id")) for line in lines if rel_id(line.get("product_id"))})
    products = {}
    if product_ids:
        for product in client.read(
            "product.product", product_ids,
            ["id", "default_code", "name", "product_tmpl_id", "categ_id", "uom_id"],
        ):
            products[product["id"]] = product

    if operation_name_fallback:
        operation_name = str(operation.get("name", "")).strip()
        keywords = []
        for suffix in ("组装", "结构"):
            if operation_name.endswith(suffix):
                operation_name = operation_name[:-len(suffix)].strip()
        if operation_name:
            keywords.append(operation_name.casefold())
        # “分度盘结构组装” maps to products such as “编带机分度盘”.
        # Match the meaningful component term after removing routing words.
        if operation_name.endswith("结构"):
            operation_name = operation_name[:-2].strip()
        if operation_name and operation_name.casefold() not in keywords:
            keywords.append(operation_name.casefold())
        if not keywords:
            raise ValueError("该组装工序缺少物料匹配名称")
        lines = [
            line for line in lines
            if any(
                _material_matches_operation(
                    operation.get("name", ""),
                    products.get(rel_id(line.get("product_id")), {}).get("name", ""),
                )
                or _material_matches_operation(
                    operation.get("name", ""),
                    products.get(rel_id(line.get("product_id")), {}).get("default_code", ""),
                )
                for _keyword in keywords
            )
        ]
        if not lines:
            raise ValueError("该组装工序在 BOM 中未找到对应物料")

    template_ids = sorted({rel_id(p.get("product_tmpl_id")) for p in products.values() if rel_id(p.get("product_tmpl_id"))})
    specifications = {}
    if template_ids:
        try:
            for template in client.read("product.template", template_ids, ["id", "spec_info"]):
                specifications[template["id"]] = str(template.get("spec_info") or "")
        except Exception as exc:
            logger.debug(f"读取机器 BOM 规格跳过: {exc}")

    stock_by_product = {}
    if product_ids:
        quants = client.search_read(
            "stock.quant",
            [("product_id", "in", product_ids), ("location_id", "=", SRC_LOCATION_ID)],
            ["product_id", "quantity"], limit=1000,
        )
        for quant in quants:
            product_id = rel_id(quant.get("product_id"))
            stock_by_product[product_id] = (
                stock_by_product.get(product_id, 0.0)
                + number(quant.get("quantity"))
            )

    items = []
    for line in lines:
        product_id = rel_id(line.get("product_id"))
        product = products.get(product_id, {})
        template_id = rel_id(product.get("product_tmpl_id"))
        uom = line.get("product_uom_id") or product.get("uom_id")
        bom_qty = number(line.get("product_qty"))
        items.append({
            "bomLineId": line["id"],
            "productId": product_id,
            "productTemplateId": template_id or 0,
            "defaultCode": str(product.get("default_code") or product_code(line.get("product_id"))),
            "name": clean_name(product.get("name") or rel_name(line.get("product_id"))),
            "specification": specifications.get(template_id, ""),
            "uomId": rel_id(uom) or 0,
            "uomName": rel_name(uom, ""),
            "bomQty": bom_qty,
            "categoryName": rel_name(product.get("categ_id"), ""),
            "brandSupplierName": "",
            "availableQty": stock_by_product.get(product_id, 0.0),
            "selected": True,
            "actualQty": bom_qty,
            "meta": {"mode": get_odoo_mode(), "source": "odoo_workorder_bom"},
        })
    return {
        "items": items,
        "productionId": production_id,
        "productionName": str(mo.get("name") or ""),
        "productName": clean_name(mo.get("product_id")),
        "productClass": workorder_product_class(mo.get("product_id")),
        "bomId": bom_id,
        "sourceLocationId": source_location_id,
        "sourceLocationName": rel_name(mo.get("location_src_id"), ""),
        "operationId": rel_id(wo.get("operation_id")) or 0,
    }


def _should_use_workorder_bom(operation, context):
    """Use the selected WO BOM for every explicitly BOM-routed operation."""
    return _operation_requires_workorder_bom(operation) or (
        isinstance(context, dict) and context.get("productClass") == "machine"
    )


def _should_fail_workorder_bom_lookup(operation, host_type):
    """Custom BOM routes fail closed instead of falling back to host BOM."""
    return _operation_requires_workorder_bom(operation) or host_type not in ("tape", "splitter")


def _operation_requires_workorder_bom(operation):
    """Recognize custom assembly routes even when an older session lacks the flag."""
    if not isinstance(operation, dict):
        return False
    return bool(operation.get("requiresBom")) or str(operation.get("code", "")).startswith(
        "worker_assembly_custom_"
    )


def models_query_tmpl_by_code(client, default_code):
    """通过 default_code 查 product.template 的 id"""
    try:
        rows = client.search_read("product.product", [("default_code", "=", default_code)],
                                   ["product_tmpl_id"], limit=1)
        if rows:
            tmpl = rows[0].get("product_tmpl_id")
            return tmpl[0] if isinstance(tmpl, (list, tuple)) else tmpl
    except Exception as e:
        logger.warning(f"models_query_tmpl_by_code 失败: {e}")
    return None


_WO_CACHE = {"data": None, "ts": 0, "marker": 0}
_WO_CACHE_LOCK = threading.Lock()
_WO_CACHE_TTL = 60  # 60秒缓存，准实时
_WORKORDER_MIN_PRODUCTION_NAME = os.getenv("WORKORDER_MIN_PRODUCTION_NAME", "").strip()


def _active_production_domain(client, lookback_start_text):
    """Build the MO domain, optionally excluding records older than a named MO."""
    active_domain = [("state", "not in", ["done", "cancel"])]
    if not _WORKORDER_MIN_PRODUCTION_NAME:
        return [("write_date", ">=", lookback_start_text)] + active_domain

    cutoff_rows = client.search_read(
        "mrp.production",
        [("name", "=", _WORKORDER_MIN_PRODUCTION_NAME)],
        ["id"], limit=1, order="id asc"
    )
    if not cutoff_rows:
        raise RuntimeError(
            f"Configured manufacturing order not found: {_WORKORDER_MIN_PRODUCTION_NAME}"
        )
    return [("id", ">=", cutoff_rows[0]["id"])] + active_domain


def get_workorders_data():
    """
    获取活跃工单列表（60秒缓存，准实时）
    过滤条件：
      - 所属 MO 未完成（state 非 done/cancel）
      - MO 在 30 天内有过任何更新（write_date >= 30 天前）
      - 工单有 operation_id + workcenter_id
      - 工序 PDF 优先但非必须
    """
    now = time.time()
    marker = reset_marker_stamp()
    with _WO_CACHE_LOCK:
        if (_WO_CACHE["data"] is not None
                and _WO_CACHE["marker"] == marker
                and (now - _WO_CACHE["ts"]) < _WO_CACHE_TTL):
            return _WO_CACHE["data"]
    mode = get_odoo_mode()
    try:
        client = get_odoo()

        # 日期下限：30 天前的 00:00 UTC（覆盖正常生产周期）
        lookback_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=30)
        lookback_start_text = lookback_start.strftime("%Y-%m-%d %H:%M:%S")

        # 第一步：找未完成的 MO（不按日期过滤，方便实时同步新 MO）
        mo_fields = ["id", "name", "bom_id", "product_id", "product_qty", "origin", "state", "write_date"]
        mo_rows = client.search_read(
            "mrp.production",
            _active_production_domain(client, lookback_start_text),
            mo_fields, limit=200, order="id desc"
        )
        # 过滤：只保留 bom 有 routing 的 MO
        bom_ids = set()
        for mo in mo_rows:
            bom_id = rel_id(mo.get("bom_id"))
            if bom_id:
                bom_ids.add(bom_id)
        boms_with_routing = set()
        if bom_ids:
            bom_data = client.read("mrp.bom", list(bom_ids), ["id", "operation_ids"])
            for b in bom_data:
                if b.get("operation_ids"):
                    boms_with_routing.add(b["id"])
        valid_mo_ids = {mo["id"] for mo in mo_rows if rel_id(mo.get("bom_id")) in boms_with_routing}
        logger.info(f"今日 MO 总数: {len(mo_rows)}, 含routing: {len(valid_mo_ids)}")

        # 第二步：找出这些 MO 的工单，且必须有 operation_id 和 workcenter_id
        wo_fields = ["id", "name", "production_id", "workcenter_id", "operation_id",
                     "product_id", "state", "qty_production", "qty_produced",
                     "qty_remaining", "duration_expected", "write_date"]
        if not valid_mo_ids:
            with _WO_CACHE_LOCK:
                if reset_marker_stamp() == marker:
                    _WO_CACHE["data"] = []
                    _WO_CACHE["ts"] = time.time()
                    _WO_CACHE["marker"] = marker
            return []
        wo_rows = client.search_read(
            "mrp.workorder",
            [("production_id", "in", list(valid_mo_ids)),
             ("workcenter_id", "!=", False),
             ("state", "not in", ["done", "cancel"])],
            wo_fields, limit=50, order="id desc"
        )
        logger.info(f"工单候选: {len(wo_rows)}条")

        # 第三步：检查工单对应的 routing.workcenter 是否有 PDF
        # 优先显示有 PDF 的，没有 PDF 的也保留（运维可能还未上传）
        # 兼容 Odoo：部分实例可能没有 mrp.routing.workcenter 模型，包裹 try/except
        ops_with_pdf = set()
        try:
            op_ids = {rel_id(wo.get("operation_id")) for wo in wo_rows if rel_id(wo.get("operation_id"))}
            if op_ids:
                op_data = client.read("mrp.routing.workcenter", list(op_ids),
                                      ["id", "worksheet", "worksheet_type"])
                for op in op_data:
                    if op.get("worksheet") or op.get("worksheet_type") == "pdf":
                        ops_with_pdf.add(op["id"])
        except Exception as routing_err:
            # 用户 Odoo 中可能没有 routing.workcenter 模型，继续执行，不阻断工单展示
            logger.debug(f"routing.workcenter 读取跳过（兼容模式）: {routing_err}")
        # 分类：有 PDF 的优先在前，没有的排在后面
        wo_with_pdf = [wo for wo in wo_rows if rel_id(wo.get("operation_id")) in ops_with_pdf]
        wo_without_pdf = [wo for wo in wo_rows if rel_id(wo.get("operation_id")) not in ops_with_pdf]
        wo_rows = wo_with_pdf + wo_without_pdf  # 有PDF的优先
        if wo_without_pdf:
            logger.info(f"工单含PDF: {len(wo_with_pdf)}条, 暂缺PDF: {len(wo_without_pdf)}条（仍显示）")

        # 获取生产单信息
        mo_ids = set()
        for wo in wo_rows:
            pid = rel_id(wo.get("production_id"))
            if pid:
                mo_ids.add(pid)

        mo_data = {}
        if mo_ids:
            mo_rows = client.read("mrp.production", list(mo_ids),
                                  ["id", "name", "bom_id", "product_id", "product_qty", "state", "origin"])
            for mo in mo_rows:
                mo_data[mo["id"]] = mo

        # Custom assembly jobs are assigned by component material, but Odoo
        # routing names can use a variant of that material name. Carry this
        # read-only MO BOM metadata into the shared matching rule.
        bom_component_names = {}
        bom_component_codes = {}
        component_bom_ids = {
            rel_id(mo.get("bom_id")) for mo in mo_data.values()
            if rel_id(mo.get("bom_id"))
        }
        if component_bom_ids:
            try:
                bom_lines = client.search_read(
                    "mrp.bom.line", [("bom_id", "in", list(component_bom_ids))],
                    ["bom_id", "product_id"], limit=2000,
                )
                product_ids = sorted({
                    rel_id(line.get("product_id")) for line in bom_lines
                    if rel_id(line.get("product_id"))
                })
                products_by_id = {
                    product["id"]: product
                    for product in client.read(
                        "product.product", product_ids, ["id", "name", "default_code"]
                    )
                } if product_ids else {}
                for line in bom_lines:
                    bom_id = rel_id(line.get("bom_id"))
                    product = products_by_id.get(rel_id(line.get("product_id")), {})
                    name = clean_name(product.get("name") or rel_name(line.get("product_id")))
                    code = str(product.get("default_code") or product_code(line.get("product_id")) or "").strip()
                    if name:
                        bom_component_names.setdefault(bom_id, []).append(name)
                    if code:
                        bom_component_codes.setdefault(bom_id, []).append(code)
            except Exception as bom_meta_error:
                logger.warning(f"读取工单 BOM 物料名称跳过（不影响工单读取）: {bom_meta_error}")

        workorders = []
        # 状态翻译
        for wo in wo_rows:
            mo_id = rel_id(wo.get("production_id"))
            mo = mo_data.get(mo_id, {})
            pid = rel_id(wo.get("product_id"))
            pcode = product_code(wo.get("product_id"))  # 传入 tuple，不要传 pid(int)

            # 确定主机类型（用产品编码，不依赖固定 ID）
            host_type = workorder_host_type(
                wo.get("product_id"), wo.get("workcenter_id")
            )
            OdooRemainingQuantityFix.apply_to_workorder_fix(wo)

            raw_state = wo.get("state", "")
            state_cn = WO_STATE_MAP.get(raw_state, raw_state)
            # 净化产品名（去掉 [编码] 前缀）
            raw_product = rel_name(wo.get("product_id"), "")
            product_name = re.sub(r"^\[[^\]]+\]\s*", "", raw_product).strip()

            workorders.append({
                "workorderId": wo["id"],
                "workorderName": wo.get("name", ""),
                "productionId": mo_id,
                "productionName": mo.get("name", ""),
                "productId": pid,
                "productCode": pcode,
                "productName": product_name or raw_product,
                "workcenterId": rel_id(wo.get("workcenter_id")),
                "workcenterName": rel_name(wo.get("workcenter_id"), ""),
                "operationId": rel_id(wo.get("operation_id")),
                "state": raw_state,
                "stateLabel": state_cn,
                "qtyProduction": number(wo.get("qty_production")),
                "qtyProduced": number(wo.get("qty_produced")),
                "remainingQty": number(wo.get("qty_remaining")),
                "hostType": host_type,
                "productClass": workorder_product_class(wo.get("product_id")),
                "bomComponentNames": bom_component_names.get(rel_id(mo.get("bom_id")), []),
                "bomComponentCodes": bom_component_codes.get(rel_id(mo.get("bom_id")), []),
            })
        with _WO_CACHE_LOCK:
            if reset_marker_stamp() == marker:
                _WO_CACHE["data"] = workorders
                _WO_CACHE["ts"] = time.time()
                _WO_CACHE["marker"] = marker
        return workorders
    except Exception as e:
        logger.warning(f"获取工单失败: {e}")
        raise


def _invalidate_runtime_caches():
    """Clear each runtime cache under the lock used by its readers."""
    with _BOM_CACHE_LOCK:
        _BOM_CACHE["data"] = None
        _BOM_CACHE["ts"] = 0
        _BOM_CACHE["key"] = None
        _BOM_CACHE["marker"] = 0
    with _DASH_CACHE_LOCK:
        _DASH_CACHE["data"] = None
        _DASH_CACHE["ts"] = 0
        _DASH_CACHE["marker"] = 0
    with _WO_CACHE_LOCK:
        _WO_CACHE["data"] = None
        _WO_CACHE["ts"] = 0
        _WO_CACHE["marker"] = 0
    with _WORKER_CACHE_LOCK:
        _WORKER_CACHE["data"] = None
        _WORKER_CACHE["ts"] = 0


# ============================================================
# ESOP — 电子作业指导书模块
# ============================================================

def get_sop_for_workorder(workorder_id: int) -> list:
    """
    查询工单的 SOP 装配指导书
    用户 Odoo 18 用 mrp.workorder.worksheet（binary）字段存储 SOP PDF。
    返回附件列表 [{id, name, fileType, version, sopUrl}]，二进制走 download 接口按需取。
    """
    client = get_odoo()
    fields = ["id", "name", "worksheet", "picture", "write_date"]
    recs = client.read("mrp.workorder", [workorder_id], fields)
    if not recs:
        return []
    w = recs[0]
    result = []
    # worksheet 字段存 PDF/装配图（base64）
    if w.get("worksheet"):
        result.append({
            "id": w["id"] * 1000,            # 虚拟 id 区分类型
            "name": (w.get("name") or "作业指导书") + "-SOP",
            "fileType": "application/pdf",
            "version": w.get("write_date", ""),
            "sopUrl": f"/api/sop/download?workorderId={workorder_id}&type=worksheet",
            "workorderId": workorder_id,
            "kind": "worksheet",
        })
    # picture 字段是图片（base64）
    if w.get("picture"):
        result.append({
            "id": w["id"] * 1000 + 1,
            "name": (w.get("name") or "作业指导书") + "-图示",
            "fileType": "image/png",
            "version": w.get("write_date", ""),
            "sopUrl": f"/api/sop/download?workorderId={workorder_id}&type=picture",
            "workorderId": workorder_id,
            "kind": "picture",
        })
    return result


def get_sop_download(workorder_id: int, kind: str) -> tuple | None:
    """从 mrp.workorder 读取 worksheet/picture 字段的二进制数据"""
    client = get_odoo()
    field_name = "worksheet" if kind == "worksheet" else "picture"
    default_mime = "application/pdf" if kind == "worksheet" else "image/png"
    try:
        recs = client.read("mrp.workorder", [workorder_id], [field_name, "name"])
        if recs:
            data = recs[0].get(field_name)
            if data:
                filename = (recs[0].get("name") or "SOP") + ("-SOP.pdf" if kind == "worksheet" else "-图示.png")
                return (default_mime, data, filename)
    except Exception as e:
        logger.warning(f"SOP download error: {e}")
    return None


def log_sop_view(attachment_id, worker_id, worker_name, workorder_id):
    """记录 SOP 查阅日志"""
    try:
        with DB_LOCK:
            c = sqlite3.connect(str(DB_FILE))
            c.execute(
                "INSERT INTO sop_view_logs (attachment_id, worker_id, worker_name, workorder_id) VALUES (?, ?, ?, ?)",
                (str(attachment_id), worker_id or "", worker_name or "", str(workorder_id or "")),
            )
            c.commit()
            c.close()
    except Exception as e:
        logger.warning(f"SOP view log error: {e}")


def _ensure_location_stock(client, product_id, location_id, required_qty):
    """Ensure a virtual production location can supply a finished move."""
    quant_ids = odoo_call(client, "stock.quant", "search", [[
        ("product_id", "=", product_id), ("location_id", "=", location_id),
    ]])
    quants = (odoo_call(client, "stock.quant", "read", [quant_ids],
                        {"fields": ["id", "quantity"]}) if quant_ids else [])
    current = sum(number(quant.get("quantity")) for quant in quants)
    deficit = max(number(required_qty) - current, 0.0)
    if deficit <= 1e-6:
        return
    if quants:
        quant = quants[0]
        new_qty = number(quant.get("quantity")) + deficit
        odoo_call(client, "stock.quant", "write", [[quant["id"]], {
            "quantity": new_qty, "inventory_quantity": new_qty,
        }])
    else:
        odoo_call(client, "stock.quant", "create", [{
            "product_id": product_id,
            "location_id": location_id,
            "quantity": deficit,
            "inventory_quantity": deficit,
        }])


def odoo_update_workorder_progress(client, workorder_id: int, qty: float, production_id: int,
                                   target_qty=None):
    """Update one WO and synchronize the MO's truly completed product count."""
    if not workorder_id or qty <= 0:
        return {"ok": False, "error": "缺少参数"}
    new_wo_qty = None
    try:
        # 1) 更新工单（WO.qty_produced 是基础字段，必须写）
        wo_rows = client.read("mrp.workorder", [workorder_id],
                              ["id", "production_id", "qty_produced",
                               "qty_production", "state"])
        if not wo_rows:
            return {"ok": False, "error": f"工单 #{workorder_id} 不存在"}
        wo = wo_rows[0]
        actual_production_id = rel_id(wo.get("production_id"))
        if production_id and actual_production_id != int(production_id):
            return {"ok": False, "error": "工单与生产订单不匹配"}
        if wo.get("state") in ("done", "cancel"):
            return {"ok": False, "error": "工单已完成或已取消"}
        current_wo_qty = float(wo.get("qty_produced", 0))
        if target_qty is None:
            new_wo_qty = current_wo_qty + float(qty)
        else:
            # A retry may reach this function after the WO write succeeded but
            # a later MO read/write failed.  Never add the same report twice.
            new_wo_qty = max(current_wo_qty, float(target_qty))
        wo_vals = {"qty_produced": new_wo_qty}
        # 自动 mark done
        if new_wo_qty >= float(wo.get("qty_production", 0)) and wo.get("state") not in ("done", "cancel"):
            wo_vals["state"] = "done"
            wo_vals["date_finished"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        client.call("mrp.workorder", "write", [[workorder_id], wo_vals])
        logger.info(f"工单#{workorder_id} qty_produced {wo.get('qty_produced',0)} -> {new_wo_qty}")
        wo_check = client.read("mrp.workorder", [workorder_id], ["qty_produced"] )
        if not wo_check or abs(float(wo_check[0].get("qty_produced", 0)) - new_wo_qty) > 1e-6:
            return {"ok": False, "error": "工单已写入但回读数量不一致",
                    "new_qty": new_wo_qty}

        # 2) A machine, including a host computer, is complete only when every
        # active WO in its MO reaches the same cumulative quantity. This also
        # includes manually added WOs whose operation_id is not populated.
        if production_id:
            all_wos = client.call("mrp.workorder", "search_read",
                [[("production_id", "=", production_id)]],
                {"fields": ["id", "qty_produced", "state"]})

            mo_rows = client.read("mrp.production", [production_id],
                                  ["id", "product_id", "move_finished_ids", "state",
                                   "product_qty", "qty_produced"])
            if not mo_rows:
                return {"ok": False, "error": f"生产订单 #{production_id} 不存在",
                        "new_qty": new_wo_qty}
            mo = mo_rows[0]
            active_wos = [w for w in all_wos if w.get("state") != "cancel"]
            quantities = [number(w.get("qty_produced")) for w in active_wos]
            route_completed = (
                min(quantities) if requires_all_route_steps(mo.get("product_id"))
                else max(quantities)
            ) if quantities else 0.0
            current_mo_qty = number(mo.get("qty_produced"))
            # Non-machine products retain the established one-step behavior.
            # Machine/host output is the minimum across every active WO, so a
            # newly added, unreported WO can correctly reduce a reversible
            # intermediate MO quantity back to zero.
            completed_qty = (
                route_completed
                if requires_all_route_steps(mo.get("product_id"))
                else max(current_mo_qty, route_completed)
            )
            product_qty = number(mo.get("product_qty"))
            if product_qty > 0:
                completed_qty = min(completed_qty, product_qty)
            is_final_production_qty = (
                product_qty > 0 and completed_qty >= product_qty - 1e-6
            )

            if abs(completed_qty - current_mo_qty) > 1e-6:
                finished_product_id = rel_id(mo.get("product_id"))
                finished_moves = client.read(
                    "stock.move", mo.get("move_finished_ids") or [],
                    ["id", "product_id", "quantity", "state", "location_id"],
                )
                primary_moves = [
                    move for move in finished_moves
                    if rel_id(move.get("product_id")) == finished_product_id
                ]
                if not primary_moves:
                    return {"ok": False, "error": "制造订单没有成品移动", "new_qty": new_wo_qty}
                move = primary_moves[0]
                move_id = move["id"]
                if completed_qty < current_mo_qty - 1e-6 and move.get("state") == "done":
                    # A done move has already posted inventory. Never reverse
                    # that stock movement implicitly when a WO is later added.
                    return {"ok": False,
                            "error": "新增工单后发现已完成成品移动，需先在 Odoo 更正已入库成品",
                            "new_qty": new_wo_qty, "completed_qty": current_mo_qty}
                if move.get("state") != "done":
                    # Keep a partially reported MO open.  Marking the finished
                    # move done for the first completed unit makes Odoo close
                    # the entire MO and automatically consume every remaining
                    # component, even when this MO still has pending units.
                    client.call("stock.move", "write", [[move_id], {
                        "quantity": completed_qty, "picked": completed_qty > 0,
                    }])
                    move_check = client.read("stock.move", [move_id], ["quantity", "state"])
                    if (not move_check or
                            abs(number(move_check[0].get("quantity")) - completed_qty) > 1e-6):
                        return {"ok": False, "error": "成品移动实际数量写入后不一致",
                                "new_qty": new_wo_qty}
                    if is_final_production_qty:
                        source_location_id = rel_id(move.get("location_id"))
                        _ensure_location_stock(
                            client, finished_product_id, source_location_id, completed_qty
                        )
                        client.call("stock.move", "write", [[move_id], {"state": "done"}])
                else:
                    delta = completed_qty - current_mo_qty
                    source_location_id = rel_id(move.get("location_id"))
                    _ensure_location_stock(client, finished_product_id, source_location_id, delta)
                    client.call("stock.move", "write", [[move_id], {
                        "quantity": completed_qty, "picked": True,
                    }])
                logger.info(
                    f"MO#{production_id} 完整工序产量={route_completed:g}, "
                    f"finished move#{move_id}={completed_qty:g}"
                )

            # 3) Read back the computed MO quantity. Never write qty_producing;
            # it drives Odoo's component inverse calculation.
            mo_check = client.read("mrp.production", [production_id],
                                  ["qty_produced"])
            if not mo_check:
                return {"ok": False, "error": "生产订单写入后无法回读",
                        "new_qty": new_wo_qty}
            mo_qty = float(mo_check[0].get("qty_produced", 0) or 0)
            # Odoo calculates the MO's final produced quantity from a done
            # finished move. For a partial report the move intentionally stays
            # open, so do not turn that expected intermediate state into a
            # failed report or force-complete the production order.
            if is_final_production_qty and abs(mo_qty - completed_qty) > 1e-6:
                return {"ok": False,
                        "error": f"生产订单已写入但回读产量不一致（期望 {completed_qty:g}，实际 {mo_qty:g}）",
                        "new_qty": new_wo_qty,
                        "completed_qty": completed_qty}
        return {"ok": True, "new_qty": new_wo_qty,
                "completed_qty": completed_qty if production_id else None}
    except Exception as e:
        logger.warning(f"Odoo 进度更新失败: {e}")
        result = {"ok": False, "error": str(e)}
        if new_wo_qty is not None:
            result["new_qty"] = new_wo_qty
        return result


def _direct_deduct_quant(client, product_id, code, actual_qty):
    """Deduct physical stock only from WH/生产前 without going negative."""
    remaining = actual_qty
    quant_ids = odoo_call(client, "stock.quant", "search", [
        [("product_id", "=", product_id), ("location_id", "=", SRC_LOCATION_ID)]
    ])
    if quant_ids:
        quants = odoo_call(client, "stock.quant", "read", [quant_ids],
                           {"fields": ["id", "quantity"]})
        # 负库存 quant 不应参与“可扣数量”计算；否则 min(负数, actual_qty)
        # 会让 remaining 反而增加，造成错误的库存结果。
        positive_quants = [q for q in quants if float(q.get("quantity", 0) or 0) > 0]
        available = sum(number(q.get("quantity")) for q in positive_quants)
        if available + 1e-6 < actual_qty:
            raise ValueError(
                f"WH/生产前库存不足（需要 {actual_qty:g}，现有 {available:g}）"
            )
        for q in positive_quants:
            if remaining <= 0:
                break
            qty = float(q["quantity"])
            take = min(qty, remaining)
            odoo_call(client, "stock.quant", "write", [[q["id"]], {
                "quantity": qty - take, "inventory_quantity": qty - take,
            }])
            remaining -= take
    else:
        raise ValueError(f"物料 {code} 在 WH/生产前没有库存")


def _restore_source_quant(client, product_id, actual_qty):
    """Restore a compensated material quantity to WH/生产前."""
    quant_ids = odoo_call(client, "stock.quant", "search", [[
        ("product_id", "=", product_id),
        ("location_id", "=", SRC_LOCATION_ID),
    ]])
    if quant_ids:
        quant = odoo_call(
            client, "stock.quant", "read", [[quant_ids[0]]],
            {"fields": ["quantity"]},
        )[0]
        restored = number(quant.get("quantity")) + number(actual_qty)
        odoo_call(client, "stock.quant", "write", [[quant_ids[0]], {
            "quantity": restored,
            "inventory_quantity": restored,
        }])
    else:
        odoo_call(client, "stock.quant", "create", [{
            "product_id": product_id,
            "location_id": SRC_LOCATION_ID,
            "quantity": number(actual_qty),
            "inventory_quantity": number(actual_qty),
        }])


def compensate_material_deduction(client, materials, production_id):
    """Undo this request's material write when WO progress did not commit."""
    if not materials or not production_id:
        return {"ok": True, "restored": []}
    mo_rows = client.read("mrp.production", [int(production_id)], ["move_raw_ids"])
    if not mo_rows:
        raise ValueError(f"制造订单 #{production_id} 不存在")
    raw_moves = client.read(
        "stock.move", mo_rows[0].get("move_raw_ids") or [],
        ["id", "product_id", "quantity", "picked"],
    )
    moves_by_product = {}
    for move in raw_moves:
        moves_by_product.setdefault(rel_id(move.get("product_id")), []).append(move)

    restored = []
    with MATERIAL_QUANTITY_LOCK:
        for material in materials:
            product_id = int(material.get("productId") or 0)
            actual_qty = number(material.get("actualQty"))
            moves = moves_by_product.get(product_id) or []
            if not moves or actual_qty <= 0:
                raise ValueError(f"物料 {material.get('defaultCode', product_id)} 无法补偿")
            remaining = actual_qty
            for move in reversed(moves):
                if remaining <= 1e-6:
                    break
                current = max(number(move.get("quantity")), 0.0)
                take = min(current, remaining)
                new_quantity = current - take
                odoo_call(client, "stock.move", "write", [[move["id"]], {
                    "quantity": new_quantity,
                    "picked": new_quantity > 0,
                }])
                remaining -= take
            if remaining > 1e-6:
                raise ValueError(f"物料 {material.get('defaultCode', product_id)} 补偿数量不足")
            _restore_source_quant(client, product_id, actual_qty)
            restored.append({"productId": product_id, "quantity": actual_qty})
    return {"ok": True, "restored": restored}


def odoo_deduct_materials(materials, production_id=None, qty=0, idempotency_key=""):
    """
    报工物料扣减 + MO 进度同步（通过 stock.move，让 Odoo UI 显示正确）
    - 对每个 raw material: 找到对应 MO 的 stock.move，累加 quantity 并 _action_done
      Odoo 自动创建 stock.move.line 并扣减 stock.quant（不会双重扣减）
    - 对 finished product: 同样找到 MO 的 move_finished_ids，累加 quantity
      MO.qty_produced（计算字段）自动从 sum of finished moves 更新
    - MO.move_raw_ids 的 quantity 也会更新，组件 UI 显示正确的已消耗数量
    """
    if MOCK_MODE:
        logger.info(f"[MOCK] 模拟物料扣减: {len(materials)}项")
        return {"ok": True, "stock_move_ids": [900000 + i for i in range(len(materials))],
                "message": "模拟物料扣减成功", "meta": {"mode": "mock", "source": "fake_odoo"}}

    if idempotency_key:
        try:
            with DB_LOCK:
                c = sqlite3.connect(str(DB_FILE))
                row = c.execute(
                    "SELECT id FROM reports WHERE idempotency_key=? AND sync_status='odoo_synced'",
                    (idempotency_key,)).fetchone()
                c.close()
                if row:
                    return {"ok": True, "stock_move_ids": [], "message": "已扣减过（幂等）",
                            "meta": {"mode": "real", "source": "idempotent"}}
        except Exception:
            pass

    client = get_odoo()
    # 这里保存真正更新过的 stock.move ID；此前误把 product_id 写入此字段，
    # 导致排查时误认为 Odoo 移动记录与报工记录不一致。
    stock_move_ids = []
    successful_material_count = 0
    errors = []

    # 0) 预加载 MO 的 raw moves（按 product_id 分组）
    raw_move_ids_by_pid = {}
    if production_id:
        try:
            mo = client.read(
                "mrp.production", [production_id], ["move_raw_ids", "location_src_id"]
            )
            if mo and rel_id(mo[0].get("location_src_id")) != SRC_LOCATION_ID:
                raise ValueError("制造订单原料库位不是 WH/生产前")
            raw_ids = mo[0].get("move_raw_ids", []) if mo else []
            if raw_ids:
                rms = client.read(
                    "stock.move", raw_ids,
                    ["id", "product_id", "state", "location_id"],
                )
                for rm in rms:
                    if rel_id(rm.get("location_id")) != SRC_LOCATION_ID:
                        continue
                    pid = rel_id(rm.get("product_id"))
                    if pid not in raw_move_ids_by_pid:
                        raw_move_ids_by_pid[pid] = []
                    raw_move_ids_by_pid[pid].append(rm["id"])
        except Exception as e:
            logger.warning(f"读 MO raw moves 失败: {e}")

    # Validate the complete material set before the first write so one missing
    # component cannot leave an avoidable partial deduction.
    required_by_product = {}
    material_code_by_product = {}
    for mat in materials:
        product_id = int(mat.get("productId") or 0)
        required_by_product[product_id] = (
            required_by_product.get(product_id, 0.0) + number(mat.get("actualQty"))
        )
        material_code_by_product[product_id] = str(mat.get("defaultCode") or "?")
    preflight_errors = []
    for product_id, required_qty in required_by_product.items():
        code = material_code_by_product[product_id]
        if product_id not in raw_move_ids_by_pid:
            preflight_errors.append(f"物料 {code}: 不属于生产订单 #{production_id} 的 WH/生产前原料移动")
            continue
        quant_ids = odoo_call(client, "stock.quant", "search", [[
            ("product_id", "=", product_id),
            ("location_id", "=", SRC_LOCATION_ID),
        ]])
        quants = (odoo_call(client, "stock.quant", "read", [quant_ids],
                            {"fields": ["quantity"]}) if quant_ids else [])
        source_quantity = sum(max(number(q.get("quantity")), 0.0) for q in quants)
        if source_quantity + 1e-6 < required_qty:
            preflight_errors.append(
                f"物料 {code}: WH/生产前库存不足（需要 {required_qty:g}，现有 {source_quantity:g}）"
            )
    if preflight_errors:
        return {"ok": False, "stock_move_ids": [], "errors": preflight_errors,
                "message": "BOM 物料预检失败",
                "meta": {"mode": "real", "source": "odoo"}}

    for mat in materials:
        product_id = mat.get("productId", 0)
        actual_qty = mat.get("actualQty", 1)
        code = mat.get("defaultCode", "?")

        if not product_id or actual_qty <= 0:
            errors.append(f"物料 {code}: 无效参数")
            continue

        try:
            quant_ids = odoo_call(client, "stock.quant", "search", [[
                ("product_id", "=", product_id),
                ("location_id", "=", SRC_LOCATION_ID),
            ]])
            quants = (odoo_call(client, "stock.quant", "read", [quant_ids],
                                {"fields": ["quantity"]}) if quant_ids else [])
            source_quantity = sum(max(number(q.get("quantity")), 0.0) for q in quants)
            if source_quantity + 1e-6 < number(actual_qty):
                raise ValueError(
                    f"WH/生产前库存不足（需要 {number(actual_qty):g}，现有 {source_quantity:g}）"
                )

            # 1) 更新 MO 的 raw stock.move.quantity
            #    只改 quantity，不改 state（否则 MO 会自动 done）
            rm_ids = raw_move_ids_by_pid.get(product_id, [])
            if not rm_ids:
                errors.append(f"物料 {code}: 不属于生产订单 #{production_id} 的原料移动")
                continue
            updated_move_ids = []
            with MATERIAL_QUANTITY_LOCK:
                for rm_id in rm_ids:
                    rm = odoo_call(
                        client, "stock.move", "read", [[rm_id], [
                            "product_uom_qty", "quantity", "should_consume_qty", "picked",
                        ]]
                    )
                    if not rm:
                        raise ValueError(f"原料移动 {rm_id} 不存在")
                    # stock.move.quantity can contain reserved stock, so it is
                    # not a safe accumulation base. Odoo's correct remaining
                    # amount gives the consumed total before this report.
                    planned_qty = float(
                        rm[0].get("product_uom_qty", 0) or 0
                    )
                    remaining_qty = OdooRemainingQuantityFix.remaining_consumption_qty(rm[0])
                    move_quantity = number(rm[0].get("quantity"))
                    # Before the first report Odoo reserves the full demand,
                    # so quantity=planned does not mean it was consumed. Once
                    # this integration writes a smaller picked quantity, that
                    # value is the cumulative actual consumption base.
                    if (rm[0].get("picked") and
                            move_quantity < planned_qty - 1e-6):
                        consumed_before = max(move_quantity, 0.0)
                    else:
                        consumed_before = max(planned_qty - remaining_qty, 0.0)
                    consumed_after = consumed_before + float(actual_qty)
                    odoo_call(client, "stock.move", "write", [[rm_id], {
                        "quantity": consumed_after,
                        "picked": True,
                    }])
                    check = odoo_call(client, "stock.move", "read", [[rm_id], [
                        "quantity", "should_consume_qty",
                    ]])
                    if not check or abs(
                        float(check[0].get("quantity", 0) or 0) - consumed_after
                    ) > 1e-6:
                        raise ValueError(
                            f"原料移动 {rm_id} 的实际消耗数量写入后不一致"
                        )
                    if abs(
                        float(check[0].get("should_consume_qty", 0) or 0)
                        - remaining_qty
                    ) > 1e-6:
                        raise ValueError(
                            f"原料移动 {rm_id} 的待消耗数量被意外改变"
                        )
                    updated_move_ids.append(rm_id)

                # Keep the stock deduction atomic with the cumulative display
                # quantity so concurrent reports cannot overwrite each other.
                _direct_deduct_quant(client, product_id, code, actual_qty)

            logger.info(f"物料 {code}({product_id}): 已扣减 {actual_qty}")
            stock_move_ids.extend(updated_move_ids)
            successful_material_count += 1

        except Exception as e:
            errors.append(f"物料 {code}: {e}")
            logger.error(f"物料 {code} 扣减失败: {e}")

    # ===== 处理 finished product 已移到 odoo_update_workorder_progress =====
    # 这里不再重复更新 finished move，由 odoo_update_workorder_progress 同步
    # （覆盖同步，保证 MO.qty_produced 始终 = sum(WO.qty_produced)）

    all_failed = len(materials) > 0 and successful_material_count == 0
    partial = len(errors) > 0 and successful_material_count > 0

    if all_failed:
        return {"ok": False, "stock_move_ids": [], "errors": errors,
                "message": f"全部 {len(materials)} 项物料扣减失败",
                "meta": {"mode": "real", "source": "odoo"}}
    if partial:
        return {"ok": True, "partial": True, "stock_move_ids": stock_move_ids, "errors": errors,
                "message": f"{successful_material_count} 项物料已扣减，{len(errors)} 项失败",
                "meta": {"mode": "real", "source": "odoo"}}
    return {"ok": True, "stock_move_ids": stock_move_ids,
            "message": f"已完成 {len(stock_move_ids)} 项物料库存扣减",
            "meta": {"mode": "real", "source": "odoo"}}


_NEGATIVE_STOCK_LOCK = threading.Lock()
_NEGATIVE_STOCK_DONE = set()


def _ensure_negative_stock_ok(client, product_id):
    """为产品模板启用负库存（线程安全）"""
    # Keep the check and the one-time Odoo writes in the same critical section.
    # This function is called only once per material in the deduction path, so
    # avoiding duplicate remote writes is worth the short lock hold.
    with _NEGATIVE_STOCK_LOCK:
        if product_id in _NEGATIVE_STOCK_DONE:
            return
        try:
            rows = odoo_call(client, "product.product", "search_read",
                             [[("id", "=", product_id)]],
                             {"fields": ["product_tmpl_id"], "limit": 1})
            if rows:
                tmpl = rows[0].get("product_tmpl_id", 0)
                if isinstance(tmpl, (list, tuple)):
                    tmpl = tmpl[0]
                odoo_call(client, "product.template", "write",
                          [[tmpl], {"allow_negative_stock": True}])
                _NEGATIVE_STOCK_DONE.add(product_id)
                logger.info(f"物料 #{product_id} tmpl#{tmpl} 负库存已启用")
        except Exception:
            pass



def server_reset_all():
    """完整重置：清空 SQLite 报工记录 + 重置 Odoo 库存/MO/WO 到初始值"""
    summary = {"sqlite_cleared": 0, "materials_cleared": 0,
               "bom_reset": 0, "mo_reset": 0, "wo_reset": 0}

    try:
        with DB_LOCK:
            c = sqlite3.connect(str(DB_FILE))
            cur = c.execute("SELECT COUNT(*) FROM reports")
            summary["sqlite_cleared"] = cur.fetchone()[0]
            # report_materials 没有 ON DELETE CASCADE，必须先显式清理，避免留下孤立物料记录。
            try:
                summary["materials_cleared"] = c.execute(
                    "SELECT COUNT(*) FROM report_materials"
                ).fetchone()[0]
                c.execute("DELETE FROM report_materials")
            except sqlite3.OperationalError:
                pass
            c.execute("DELETE FROM reports")
            try:
                c.execute("DELETE FROM sop_view_logs")
            except sqlite3.OperationalError:
                pass
            c.commit()
            c.close()
    except Exception as e:
        logger.warning(f"清空 SQLite 失败: {e}")

    client = get_odoo()
    if not client:
        return summary

    ALL_BOM_CODES = [
        "P04725", "P04726", "P05346", "P05347", "P05348",
        "P05350", "P05351", "P05352", "P05353", "P05931",
    ]
    PROD_BEFORE_QTY = {
        "P04725": 200, "P04726": 200, "P05346": 200, "P05347": 200,
        "P05348": 200, "P05350": 200, "P05351": 200, "P05352": 200,
        "P05353": 200, "P05930": 50, "P05931": 100,
    }

    def _call(model, method, args=None, kwargs=None):
        return odoo_call(client, model, method, args, kwargs)

    def _reset_quant(product_id, location_id, target_qty):
        qids = _call("stock.quant", "search", [[("product_id", "=", product_id), ("location_id", "=", location_id)]])
        if qids:
            for qid in qids[1:]:
                _call("stock.quant", "write", [[qid], {"quantity": 0, "inventory_quantity": 0}])
            _call("stock.quant", "write", [[qids[0]], {"quantity": target_qty, "inventory_quantity": target_qty}])
        else:
            _call("stock.quant", "create", [{"product_id": product_id, "location_id": location_id,
                                            "quantity": target_qty, "inventory_quantity": target_qty}])

    for code in ALL_BOM_CODES + ["P05930"]:
        prod = _call("product.product", "search_read",
                      [[("default_code", "=", code)]], {"fields": ["id"], "limit": 1})
        if not prod:
            continue
        pid = prod[0]["id"]
        prod_before = PROD_BEFORE_QTY.get(code, 200)
        try:
            _reset_quant(pid, 8, 100)
            _reset_quant(pid, 17, prod_before)
            summary["bom_reset"] += 1
        except Exception as e:
            logger.warning(f"重置 {code} 库存失败: {e}")

    mos = _call("mrp.production", "search", [[("state", "not in", ["done", "cancel"])]])
    for mo_id in mos:
        try:
            mo = _call("mrp.production", "read", [[mo_id]],
                       {"fields": ["move_raw_ids", "move_finished_ids", "product_qty"]})
            for rm_id in (mo[0].get("move_raw_ids") or []):
                try:
                    _call("stock.move", "write", [[rm_id], {"quantity": 0}])
                except Exception:
                    pass
            for fm_id in (mo[0].get("move_finished_ids") or []):
                try:
                    _call("stock.move", "write", [[fm_id], {"quantity": 0, "state": "assigned"}])
                except Exception:
                    pass
            try:
                _call("mrp.production", "write", [[mo_id], {"qty_producing": mo[0].get("product_qty", 0)}])
            except Exception:
                pass
            summary["mo_reset"] += 1
        except Exception as e:
            logger.warning(f"重置 MO#{mo_id} 失败: {e}")

    wos = _call("mrp.workorder", "search", [[("state", "not in", ["done", "cancel"])]])
    for wid in wos:
        try:
            _call("mrp.workorder", "write", [[wid], {"qty_produced": 0}])
            summary["wo_reset"] += 1
        except Exception:
            pass

    # 清零 WO 可能触发 Odoo 重算成品移动状态，最后再恢复初始的 assigned 状态。
    for mo_id in mos:
        try:
            mo = _call("mrp.production", "read", [[mo_id]], {"fields": ["move_finished_ids"]})
            for fm_id in (mo[0].get("move_finished_ids") or []) if mo else []:
                _call("stock.move", "write", [[fm_id], {"quantity": 0, "state": "assigned"}])
        except Exception:
            pass

    # Odoo 已重置后立即失效服务端缓存，避免面板继续显示旧进度/BOM。
    _invalidate_runtime_caches()

    # Persist the reset event so a separately running panel process drops its
    # in-memory Odoo caches on the next request as well.
    try:
        RESET_MARKER_FILE.touch()
    except OSError as marker_err:
        logger.warning(f"写入重置标记失败: {marker_err}")

    # This cache is used by report validation and must not retain identifiers
    # from the pre-reset dashboard.
    with _order_ids_lock:
        _order_ids_cache["ids"] = set()
        _order_ids_cache["ts"] = 0
        _order_ids_cache["marker"] = 0

    return summary


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    @staticmethod
    def _allowed_origin(origin):
        if not origin:
            return None
        for prefix in ("http://192.168.", "http://127.0.0.", "http://localhost"):
            if origin.startswith(prefix):
                return origin
        return None

    def end_headers(self):
        origin = self._allowed_origin(self.headers.get("Origin", ""))
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        origin = self._allowed_origin(self.headers.get("Origin", ""))
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key")
        self.end_headers()

    def panel_worker(self):
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except (ValueError, CookieError):
            return None
        morsel = cookies.get(PANEL_SESSION_COOKIE)
        session_worker = _panel_session_worker(morsel.value if morsel else "")
        if not session_worker:
            return None
        # A manager may alter an employee's jobs while the panel remains open.
        # Resolve the signed identity against the management service so the
        # next API request observes the new allowed operations. If that
        # service is briefly unavailable, the signed session remains usable.
        if session_worker.get("source") == "report_admin":
            current_worker = get_worker_by_id(session_worker["id"])
            if current_worker:
                return current_worker
        return session_worker

    def require_panel_worker(self):
        worker = self.panel_worker()
        if worker is None:
            self.write_json({"ok": False, "error": "请先登录员工账号"}, status=HTTPStatus.UNAUTHORIZED)
        return worker

    def redirect_to_login(self):
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", "/login.html")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        qs = urlparse(self.path).query
        params = {}
        if qs:
            for pair in qs.split("&"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    params[k] = v

        if path.startswith("/api/"):
            if path == "/api/health":
                return self._route_get_api(path, params, None)
            if is_report_admin_read_path(path) and check_report_admin_auth(self):
                return self._route_get_api(path, params, None, internal=True)
            worker = self.require_panel_worker()
            if worker is None:
                return
            return self._route_get_api(path, params, worker)
        ext = os.path.splitext(path)[1].lower()
        if path == "/":
            if self.panel_worker() is None:
                return self.redirect_to_login()
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/worker-report.html")
            self.end_headers()
            return
        if path == "/login":
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/login.html")
            self.end_headers()
            return
        if path == "/login.html" and self.panel_worker() is not None:
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/worker-report.html")
            self.end_headers()
            return
        if path == "/worker-report.html" and self.panel_worker() is None:
            return self.redirect_to_login()
        if path == "/worker-report.html":
            return super().do_GET()
        if path == "/login.html":
            return super().do_GET()
        if ext in WHITE_EXT:
            return super().do_GET()
        self.send_error(HTTPStatus.NOT_FOUND)

    def _route_get_api(self, path, params, panel_worker=None, internal=False):
        if path == "/api/health":
            self.write_json({"ok": True, "mode": get_odoo_mode()})
        elif path == "/api/session":
            if panel_worker is None:
                self.write_json({"ok": False, "error": "请先登录员工账号"}, status=HTTPStatus.UNAUTHORIZED)
                return
            self.write_json({"ok": True, "data": panel_worker})
        elif path == "/api/workers":
            workers = db_workers() if internal else [panel_worker]
            self.write_json({"ok": True, "data": workers,
                             "meta": {"mode": get_odoo_mode(), "count": len(workers),
                                      "source": "internal" if internal else "session"}})
        elif path == "/api/reports":
            reports = load_reports()
            if not internal:
                reports = [
                    report for report in reports
                    if str(report.get("worker_id", "")) == str(panel_worker["id"])
                ]
            self.write_json({"ok": True, "data": [_normalize_report(r) for r in reports]})
        elif path == "/api/report-stats":
            payload = self.report_stats_payload(panel_worker)
            status = HTTPStatus.OK if payload.get("ok") else HTTPStatus.INTERNAL_SERVER_ERROR
            self.write_json(payload, status=status)
        elif path == "/api/operations":
            allowed = set(panel_worker.get("operationCodes", []))
            ops = [op for op in get_operations_for_worker(panel_worker) if op["code"] in allowed]
            self.write_json({"ok": True, "data": ops,
                             "meta": {"mode": get_odoo_mode(), "count": len(ops)}})
        elif path == "/api/workorders":
            try:
                wos = get_workorders_data() if internal else panel_accessible_workorders(panel_worker)
                if not internal:
                    # The server owns employee-operation-to-WO authorization.
                    # The client uses this result rather than duplicating matching rules.
                    wos = [
                        {
                            **workorder,
                            "allowedOperationCodes": panel_worker_matching_operation_codes(
                                panel_worker, workorder
                            ),
                        }
                        for workorder in wos
                    ]
                self.write_json({"ok": True, "data": wos,
                                 "meta": {"mode": get_odoo_mode(), "count": len(wos),
                                          "source": "internal" if internal else "session"}})
            except Exception as e:
                self.write_json({"ok": False, "error": f"获取工单失败: {e}"},
                                status=HTTPStatus.INTERNAL_SERVER_ERROR)
        elif path == "/api/bom":
            host_type = params.get("hostType", params.get("host_type", ""))
            workorder_id = params.get("workorderId", params.get("workorder_id", ""))
            operation_code = params.get("operationCode", params.get("operation_code", ""))
            nocache = params.get("nocache", "0") in ("1", "true", "yes")
            if not workorder_id or not panel_worker_can_access_workorder(panel_worker, workorder_id):
                self.write_json({"ok": False, "error": "该工单不属于当前员工允许的工序"},
                                status=HTTPStatus.FORBIDDEN)
                return
            if workorder_id:
                try:
                    operation = operation_for_worker(panel_worker, operation_code)
                    if not operation:
                        raise ValueError("当前员工未绑定所选工序")
                    context = get_workorder_bom_data(workorder_id, operation)
                    if _should_use_workorder_bom(operation, context):
                        items = context.pop("items")
                        self.write_json({
                            "ok": True,
                            "data": items,
                            "meta": {
                                "mode": get_odoo_mode(),
                                "count": len(items),
                                "nocache": nocache,
                                **context,
                            },
                        })
                        return
                except Exception as e:
                    if not _should_fail_workorder_bom_lookup(operation, host_type):
                        logger.debug(f"主机工单沿用主机 BOM 查询: {e}")
                    else:
                        logger.error(f"工单 BOM 查询异常: {e}")
                        self.write_json({
                            "ok": False,
                            "error": f"工单 BOM 数据加载失败: {e}",
                            "data": [],
                        }, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                        return
            if not host_type and workorder_id:
                # 根据工单 ID 推断主机类型
                try:
                    wos = get_workorders_data()
                    for wo in wos:
                        if str(wo.get("workorderId")) == str(workorder_id):
                            host_type = wo.get("hostType", "")
                            break
                except Exception:
                    pass
            if host_type not in ("tape", "splitter"):
                self.write_json({"ok": False, "error": "需要指定 hostType=tape 或 hostType=splitter"},
                                status=HTTPStatus.BAD_REQUEST)
                return
            try:
                # nocache=1 时强制清掉缓存，重新从 Odoo 拉最新
                if nocache:
                    with _BOM_CACHE_LOCK:
                        _BOM_CACHE["data"] = None
                        _BOM_CACHE["ts"] = 0
                        _BOM_CACHE["key"] = None
                items = get_bom_data(host_type)
                self.write_json({"ok": True, "data": items,
                                 "meta": {"mode": get_odoo_mode(), "hostType": host_type,
                                          "count": len(items), "nocache": nocache}})
            except Exception as e:
                logger.error(f"BOM 查询异常: {e}")
                self.write_json({"ok": False, "error": f"BOM数据加载失败: {e}",
                                 "data": [], "meta": {"mode": get_odoo_mode(), "hostType": host_type}},
                                status=HTTPStatus.INTERNAL_SERVER_ERROR)

        elif path == "/api/dashboard":
            self.write_json(self.dashboard_payload(panel_worker))
        elif path == "/api/order-summary":
            # 订单进度摘要（从工单汇总 MO 进度）
            try:
                allowed_workorder_ids = {
                    str(workorder.get("workorderId"))
                    for workorder in (get_workorders_data() if internal else panel_accessible_workorders(panel_worker))
                }
                client = get_odoo()
                mo_domain = [("state", "not in", ["done", "cancel"])]
                mo_ids = client.call("mrp.production", "search", [mo_domain])
                if not mo_ids:
                    self.write_json({"ok": True, "data": [], "meta": {"mode": get_odoo_mode(), "count": 0}})
                    return
                mos = client.read("mrp.production", mo_ids, [
                    "name", "origin", "state", "product_id", "product_qty",
                    "qty_produced", "date_start", "write_date"
                ])
                # 拉所有未完成工单
                wo_domain = [("production_id", "in", mo_ids),
                             ("state", "not in", ["done", "cancel"])]
                wo_ids = client.call("mrp.workorder", "search", [wo_domain])
                wo_by_mo = {}
                if wo_ids:
                    wos = client.read("mrp.workorder", wo_ids, [
                        "id", "name", "production_id", "state", "qty_produced", "qty_production",
                        "workcenter_id", "operation_id"
                    ])
                    for w in wos:
                        pid = rel_id(w.get("production_id"))
                        if pid not in wo_by_mo:
                            wo_by_mo[pid] = []
                        wo_by_mo[pid].append({
                            "workorderId": w["id"],
                            "name": w.get("name", ""),
                            "state": w.get("state", ""),
                            "qtyProduced": float(w.get("qty_produced", 0) or 0),
                            "qtyProduction": float(w.get("qty_production", 0) or 0),
                            "workcenterName": rel_name(w.get("workcenter_id"), ""),
                        })
                items = []
                for mo in mos:
                    mo_id = mo["id"]
                    pid = rel_id(mo.get("product_id"))
                    wos = [
                        workorder for workorder in wo_by_mo.get(mo_id, [])
                        if str(workorder.get("workorderId")) in allowed_workorder_ids
                    ]
                    if not wos:
                        continue
                    # MO 实际进度 = 已完成工单的 qty_produced 之和 / qty_production 之和
                    total_done = sum(w["qtyProduced"] for w in wos)
                    total_target = sum(w["qtyProduction"] for w in wos) or float(mo.get("product_qty", 1))
                    progress_pct = (total_done / total_target * 100) if total_target > 0 else 0
                    items.append({
                        "productionId": mo_id,
                        "productionName": mo.get("name", ""),
                        "productName": rel_name(mo.get("product_id"), ""),
                        "productQty": float(mo.get("product_qty", 0) or 0),
                        "qtyProduced": total_done,  # 从工单汇总（Odoo qty_produced 是计算字段）
                        "progress": round(progress_pct, 1),
                        "state": mo.get("state", ""),
                        "stateLabel": MO_STATE_MAP.get(mo.get("state", ""), mo.get("state", "")),
                        "workorders": wos,
                    })
                self.write_json({"ok": True, "data": items,
                                 "meta": {"mode": get_odoo_mode(), "count": len(items)}})
            except Exception as e:
                logger.error(f"order-summary 异常: {e}")
                self.write_json({"ok": False, "error": f"订单摘要失败: {e}",
                                 "data": [], "meta": {"mode": get_odoo_mode()}},
                                status=HTTPStatus.INTERNAL_SERVER_ERROR)

        # ---- ESOP 模块 API ----
        elif path == "/api/sop/list":
            wo_id_str = params.get("workorderId", params.get("workorder_id", ""))
            if not wo_id_str:
                self.write_json({"ok": False, "error": "缺少 workorderId 参数"},
                                status=HTTPStatus.BAD_REQUEST)
                return
            if not panel_worker_can_access_workorder(panel_worker, wo_id_str):
                self.write_json({"ok": False, "error": "该工单不属于当前员工允许的工序"},
                                status=HTTPStatus.FORBIDDEN)
                return
            try:
                wo_id = int(wo_id_str)
                sop_list = get_sop_for_workorder(wo_id)
                self.write_json({"ok": True, "data": sop_list, "meta": {"mode": get_odoo_mode(), "count": len(sop_list)}})
            except Exception as e:
                self.write_json({"ok": False, "error": f"SOP查询失败: {e}"},
                                status=HTTPStatus.INTERNAL_SERVER_ERROR)

        elif path == "/api/sop/download":
            wo_id_str = params.get("workorderId", "")
            kind = params.get("type", "worksheet")
            if not wo_id_str:
                self.send_error(HTTPStatus.BAD_REQUEST, "缺少 workorderId")
                return
            if not panel_worker_can_access_workorder(panel_worker, wo_id_str):
                self.send_error(HTTPStatus.FORBIDDEN, "该工单不属于当前员工允许的工序")
                return
            try:
                wo_id = int(wo_id_str)
                file_data = get_sop_download(wo_id, kind)
                if file_data is None:
                    self.send_error(HTTPStatus.NOT_FOUND, "该工单无 SOP 附件")
                    return
                mime, b64data, filename = file_data
                import base64
                raw = base64.b64decode(b64data) if b64data else b""
                # 中文文件名用 RFC 5987 编码
                from urllib.parse import quote
                ascii_name = "SOP.pdf" if kind == "worksheet" else "SOP.png"
                encoded_name = quote(filename)
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Content-Disposition",
                                 f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded_name}")
                self.send_header("Cache-Control", "max-age=3600")
                self.end_headers()
                self.wfile.write(raw)
            except ValueError:
                self.send_error(HTTPStatus.BAD_REQUEST, "workorderId 必须为数字")
            except Exception as e:
                logger.error(f"SOP下载异常: {e}")
                self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))

        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        path = urlparse(self.path).path
        # 请求体大小限制 64KB
        length = int(self.headers.get("Content-Length", 0))
        if length > 65536:
            self.send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Body too large")
            return
        if path == "/api/login":
            return self.handle_panel_login(length)
        if path == "/api/logout":
            self.handle_panel_logout()
            return
        if path == "/api/workers/sync":
            if not check_report_admin_auth(self):
                self.write_json({"ok": False, "error": "未授权：无效的后台同步密钥"},
                                status=HTTPStatus.UNAUTHORIZED)
                return
            return self.handle_worker_sync_post()
        if path == "/api/reports":
            panel_worker = self.require_panel_worker()
            if panel_worker is None:
                return
            self.handle_report_post(panel_worker)
            return
        if path == "/api/sop/view-log":
            panel_worker = self.require_panel_worker()
            if panel_worker is None:
                return
            try:
                raw = self.rfile.read(length)
                body = json.loads(raw) if raw else {}
                if not isinstance(body, dict):
                    raise ValueError("日志数据必须为对象")
                log_sop_view(
                    body.get("attachmentId", ""),
                    panel_worker["id"],
                    panel_worker["name"],
                    body.get("workorderId", ""),
                )
                self.write_json({"ok": True})
            except Exception as exc:
                self.write_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        if not check_auth(self):
            self.write_json({"ok": False, "error": "未授权：缺少或无效的 API Key"},
                            status=HTTPStatus.UNAUTHORIZED)
            return
        if path == "/api/workers":
            self.handle_worker_post()
        elif path == "/api/reset":
            # 远程触发：清空本地 SQLite + Odoo 库存/MO/WO 重置
            # 可选 confirm=true 才执行（防止误触）
            try:
                raw = self.rfile.read(length)
                body = json.loads(raw) if raw else {}
            except Exception:
                body = {}
            if not body.get("confirm"):
                self.write_json({"ok": False, "error": "需要 confirm=true 才执行"},
                                status=HTTPStatus.BAD_REQUEST)
                return
            logger.warning(f"[API] 远程触发 reset from {self.client_address}")
            try:
                summary = server_reset_all()
                self.write_json({"ok": True, "data": summary})
            except Exception as e:
                logger.error(f"[API] reset 失败: {e}")
                self.write_json({"ok": False, "error": str(e)},
                                status=HTTPStatus.INTERNAL_SERVER_ERROR)
        elif path == "/api/sop/view-log":
            # SOP 查看日志上报（无需 token，日志而已）
            try:
                raw = self.rfile.read(length)
                body = json.loads(raw) if raw else {}
                log_sop_view(
                    body.get("attachmentId", ""),
                    body.get("workerId", ""),
                    body.get("workerName", ""),
                    body.get("workorderId", ""),
                )
                self.write_json({"ok": True})
            except Exception as e:
                self.write_json({"ok": False, "error": str(e)})
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def handle_panel_login(self, length):
        try:
            raw = self.rfile.read(length)
            payload = json.loads(raw) if raw else {}
            if not isinstance(payload, dict):
                raise ValueError("登录数据必须为对象")
            username = str(payload.get("username", "")).strip()
            password = payload.get("password")
            if not username or not isinstance(password, str):
                self.write_json({"ok": False, "error": "请输入账号和密码"}, status=HTTPStatus.BAD_REQUEST)
                return
            worker, error, status = authenticate_panel_account(username, password)
            if worker is None:
                self.write_json({"ok": False, "error": error}, status=status)
                return
            token = _panel_session_token(worker)
            self.write_json(
                {"ok": True, "data": worker},
                headers={
                    "Set-Cookie": (
                        f"{PANEL_SESSION_COOKIE}={token}; Max-Age={PANEL_SESSION_TTL_SECONDS}; "
                        "Path=/; HttpOnly; SameSite=Lax"
                    )
                },
            )
        except (json.JSONDecodeError, ValueError) as exc:
            self.write_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except RuntimeError as exc:
            self.write_json({"ok": False, "error": str(exc)}, status=HTTPStatus.SERVICE_UNAVAILABLE)
        except Exception:
            logger.exception("SOP 面板登录处理异常")
            self.write_json({"ok": False, "error": "登录服务暂时不可用，请稍后重试"},
                            status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def handle_panel_logout(self):
        self.write_json(
            {"ok": True},
            headers={
                "Set-Cookie": f"{PANEL_SESSION_COOKIE}=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax"
            },
        )

    def log_message(self, format, *args):
        logger.info("%s - %s", self.client_address[0], format % args)

    # ---- API 实现 ----

    def order_summary_payload(self):
        try:
            data = load_dashboard()
            orders = [{
                "id": row.get("order", ""),
                "customerCode": row.get("customerCode", ""),
                "customer": row.get("customer", ""),
                "product": row.get("machine", ""),
                "code": row.get("code", ""),
                "spec": row.get("spec", ""),
                "qty": row.get("qty", ""),
                "uom": row.get("uom", ""),
                "remaining": row.get("remaining", ""),
                "remark": row.get("remark", ""),
                "date": row.get("date", ""),
                "updated": row.get("updated", ""),
                "priority": row.get("priority", ""),
                "status": row.get("delivery", ""),
            } for row in data.get("deliveryRows", [])]
            return {"ok": True, "data": orders,
                    "meta": {"mode": get_odoo_mode(), "source": "odoo"}}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def handle_report_post(self, panel_worker):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            report = json.loads(body)

            if not isinstance(report, dict):
                raise ValueError("报工数据必须为对象")
            # The authenticated session is authoritative. Ignore any worker
            # identity supplied by the browser.
            report["workerId"] = panel_worker["id"]
            report["workerName"] = panel_worker["name"]
            report["workerTeam"] = panel_worker.get("team", "")
            report["odooEmployeeId"] = panel_worker.get("odooEmployeeId", 0)

            mode = get_odoo_mode()

            # === Request idempotency: replay completed requests, retry failures ===
            idempotency_key = report.get("idempotencyKey", "")
            existing_idempotent_report = None
            if idempotency_key:
                existing_idempotent_report = db_get_report_by_idempotency(idempotency_key)
                if (existing_idempotent_report
                        and str(existing_idempotent_report.get("worker_id", "")) != panel_worker["id"]):
                    self.write_json({"ok": False, "error": "幂等键与当前员工不匹配"},
                                    status=HTTPStatus.CONFLICT)
                    return
                existing_status = (
                    existing_idempotent_report.get("sync_status")
                    if existing_idempotent_report else ""
                )
                if existing_idempotent_report and existing_status == "odoo_pending":
                    self.write_json({
                        "ok": False,
                        "error": "该报工的 Odoo 同步状态尚未确认，为避免重复扣料，未再次执行",
                        "data": _normalize_report(existing_idempotent_report),
                    }, status=HTTPStatus.CONFLICT)
                    return
                if (existing_idempotent_report
                        and existing_status not in ("odoo_partial", "odoo_failed")):
                    logger.info(f"幂等请求: {idempotency_key} - 返回已有结果")
                    self.write_json({
                        "ok": True,
                        "data": _normalize_report(existing_idempotent_report),
                        "meta": {"mode": mode, "source": "idempotent_replay",
                                 "message": "该报工已处理过，返回已有结果"}
                    })
                    return

            # === 输入校验 ===
            required = ["workerId", "workerName", "operation", "qty", "date", "time"]
            for field in required:
                if not report.get(field):
                    self.write_json({"ok": False, "error": f"缺少必填字段: {field}"},
                                    status=HTTPStatus.BAD_REQUEST)
                    return

            worker_id = panel_worker["id"]
            worker = panel_worker

            # 新字段: 工单ID
            workorder_id = str(report.get("workorderId", report.get("orderId", "")))
            production_id = str(report.get("productionId", ""))

            # A report without an Odoo work order can only change the local
            # report table and cannot be synchronized to a production order.
            # Reject it before any material deduction takes place.
            if (not workorder_id.isdigit() or int(workorder_id) <= 0
                    or not production_id.isdigit() or int(production_id) <= 0):
                self.write_json({"ok": False, "error": "请先选择有效的工单后再报工"},
                                status=HTTPStatus.BAD_REQUEST)
                return

            if not panel_worker_can_access_workorder(worker, workorder_id):
                self.write_json({"ok": False, "error": "该工单不属于当前员工允许的工序"},
                                status=HTTPStatus.FORBIDDEN)
                return

            if mode == "real":
                try:
                    check_client = get_odoo()
                    check_rows = check_client.read(
                        "mrp.workorder", [int(workorder_id)],
                        ["production_id", "state", "name", "product_id", "workcenter_id"]
                    )
                    if not check_rows:
                        raise ValueError("工单不存在")
                    actual_production_id = rel_id(check_rows[0].get("production_id"))
                    if actual_production_id != int(production_id):
                        raise ValueError("工单与生产订单不匹配")
                    if check_rows[0].get("state") in ("done", "cancel"):
                        raise ValueError("工单已完成或已取消")
                except Exception as check_err:
                    self.write_json({"ok": False, "error": f"工单校验失败: {check_err}"},
                                    status=HTTPStatus.BAD_REQUEST)
                    return

            operation = str(report["operation"])
            op_info = operation_for_worker(worker, operation)
            selected_role, role_operation = role_for_worker_operation(worker, operation)
            if not op_info:
                self.write_json({"ok": False, "error": f"无效工序: {operation}"},
                                status=HTTPStatus.BAD_REQUEST)
                return
            if mode == "real" and op_info.get("mockOnly"):
                self.write_json({"ok": False, "error": "测试工序仅可在 Mock 模式使用"},
                                status=HTTPStatus.BAD_REQUEST)
                return
            if operation not in set(worker.get("operationCodes") or []):
                self.write_json({
                    "ok": False,
                    "error": "当前工人未绑定该工序，不能提交报工",
                }, status=HTTPStatus.FORBIDDEN)
                return
            if worker.get("jobRoles") and not role_operation:
                self.write_json({"ok": False, "error": "所选具体工艺不属于当前岗位授权"}, status=HTTPStatus.FORBIDDEN)
                return
            if selected_role:
                report["jobRoleCode"] = str(selected_role.get("code", ""))
                report["jobRoleName"] = str(selected_role.get("name", ""))
                report["processCode"] = str(role_operation.get("processCode", operation))
                report["processName"] = str(role_operation.get("processName", role_operation.get("name", report.get("operationLabel", operation))))
            machine_operation = False
            assembly_operation = _operation_requires_workorder_bom(op_info)
            machine_assembly = False
            if mode == "real":
                # Reuse the pre-filtered workorder view so a custom component
                # route has the same BOM-backed permission at display time and
                # at report submission time.
                workorder_view = next(
                    (
                        item for item in get_workorders_data()
                        if str(item.get("workorderId")) == str(workorder_id)
                    ),
                    {
                        "workorderName": check_rows[0].get("name", ""),
                        "hostType": workorder_host_type(
                            check_rows[0].get("product_id"),
                            check_rows[0].get("workcenter_id"),
                        ),
                        "productClass": workorder_product_class(check_rows[0].get("product_id")),
                    },
                )
                machine_operation = workorder_view["productClass"] == "machine"
                assembly_operation = assembly_operation or (
                    op_info.get("name") == "组装"
                    and workorder_view["productClass"] in {"machine", "host"}
                )
                required_product_class = worker_required_product_class(worker)
                if (required_product_class
                        and workorder_view["productClass"] != required_product_class):
                    self.write_json({
                        "ok": False,
                        "error": (
                            "组装部员工只能选择主机类工单"
                            if required_product_class == "host"
                            else "生产车间员工只能选择机器类工单"
                        ),
                    }, status=HTTPStatus.FORBIDDEN)
                    return
                if not operation_matches_workorder(op_info, workorder_view):
                    self.write_json({
                        "ok": False,
                        "error": "所选工单不属于当前工序",
                    }, status=HTTPStatus.BAD_REQUEST)
                    return

            qty = report["qty"]
            # Production reports are counted in whole units. Reject decimal
            # quantities at the API boundary instead of letting a fractional
            # value propagate into WO/MO quantities and Odoo inverse fields.
            if isinstance(qty, bool) or not isinstance(qty, (int, float)):
                self.write_json({"ok": False, "error": "Report quantity must be an integer"},
                                status=HTTPStatus.BAD_REQUEST)
                return
            qty_value = float(qty)
            if not math.isfinite(qty_value) or qty_value <= 0 or not qty_value.is_integer():
                self.write_json({"ok": False, "error": "Report quantity must be a positive integer"},
                                status=HTTPStatus.BAD_REQUEST)
                return
            qty = int(qty_value)
            report["qty"] = qty
            if not isinstance(qty, (int, float)) or qty <= 0:
                self.write_json({"ok": False, "error": "数量必须是正数"},
                                status=HTTPStatus.BAD_REQUEST)
                return

            # === 物料校验 ===
            materials = report.get("materials", [])
            host_type = op_info.get("hostType")
            if assembly_operation:
                if not isinstance(materials, list) or not materials:
                    self.write_json({
                        "ok": False,
                        "error": "机器组装工序必须确认该制造订单的 BOM 物料清单",
                    }, status=HTTPStatus.BAD_REQUEST)
                    return
                try:
                    bom_context = get_workorder_bom_data(workorder_id, op_info)
                    expected_items = bom_context.get("items", [])
                    # Component identities remain authoritative from Odoo, while
                    # the operator-confirmed actual usage may exceed the default.
                    materials = normalize_machine_bom_materials(materials, expected_items)
                    report["materials"] = materials
                except Exception as bom_err:
                    self.write_json({
                        "ok": False,
                        "error": f"机器组装 BOM 校验失败: {bom_err}",
                    }, status=HTTPStatus.BAD_REQUEST)
                    return
            elif materials:
                try:
                    bom_context = get_workorder_bom_data(workorder_id, op_info)
                    expected_items = bom_context.get("items", [])
                    # Any routing step may consume materials.  Confirmation of
                    # the selected MO's BOM, not the operation name, authorizes
                    # the one-time inventory deduction.
                    materials = normalize_machine_bom_materials(materials, expected_items)
                    report["materials"] = materials
                except Exception as bom_err:
                    self.write_json({
                        "ok": False,
                        "error": f"工序物料清单校验失败: {bom_err}",
                    }, status=HTTPStatus.BAD_REQUEST)
                    return
            if materials:
                # 校验每种物料的 actualQty > 0
                # 注意：物料清单由前端 BOM 弹窗从 Odoo mrp.bom 动态拉取，已保证属于当前主机类型，
                #       服务端不再硬编码 TAPE/SPLITTER BOM 校验，避免 Odoo 加新物料时拦截。
                for mat in materials:
                    code = mat.get("defaultCode", "")
                    actual_qty = mat.get("actualQty", 0)
                    if not isinstance(actual_qty, (int, float)) or actual_qty <= 0:
                        self.write_json({"ok": False, "error": f"物料 {code} 实际使用数量必须为正数"},
                                        status=HTTPStatus.BAD_REQUEST)
                        return
                    if not mat.get("productId"):
                        self.write_json({"ok": False, "error": f"物料 {code} 缺少产品ID"},
                                        status=HTTPStatus.BAD_REQUEST)
                        return

            # === 日期格式校验 ===
            date_str = str(report["date"])
            try:
                datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                self.write_json({"ok": False, "error": "日期格式错误，需要 YYYY-MM-DD"},
                                status=HTTPStatus.BAD_REQUEST)
                return

            # Only the same request key retries an incomplete synchronization.
            # A new key is a new report, even for the same worker/work order,
            # operation and day.
            existing_workorder_report = existing_idempotent_report
            retry_existing_report = None
            if existing_workorder_report:
                old_status = existing_workorder_report.get("sync_status", "local")
                if old_status in ("odoo_partial", "odoo_failed"):
                    # A retry only re-runs the WO/MO progress synchronization.
                    # Material deductions from the original attempt are never
                    # repeated, even when the client sends the BOM again.
                    retry_existing_report = existing_workorder_report

            # === 构建记录 ===
            report["id"] = str(uuid.uuid4())
            report["timestamp"] = int(datetime.now(LOCAL_TZ).timestamp() * 1000)
            report.setdefault("operationLabel", op_info.get("name", report["operation"]))
            report.setdefault("qualified", qty)
            report.setdefault("hours", 0)
            report.setdefault("remark", "")
            report.setdefault("workerTeam", "")
            report.setdefault("orderCustomer", "")
            report.setdefault("orderProduct", "")
            report.setdefault("productionId", production_id)
            report.setdefault("workorderId", workorder_id)
            report.setdefault("odooEmployeeId", report.get("odooEmployeeId", 0))
            report.setdefault("idempotencyKey", idempotency_key)
            report.setdefault("odooReportId", "")
            report.setdefault("odooStockMoveIds", "")
            report.setdefault("syncStatus", "local")
            report.setdefault("materialSyncStatus", "unknown")
            report.setdefault("odooProgressQty", None)
            report.setdefault("errorMessage", "")

            retry_material_status = "not_required"
            retry_material_required = False
            retry_progress_target = None
            if retry_existing_report:
                # Reuse the original report row and its original quantities and
                # identifiers.  The incoming payload is only a retry trigger.
                report["id"] = retry_existing_report["id"]
                report["qty"] = retry_existing_report["qty"]
                qty = retry_existing_report["qty"]
                report["productionId"] = retry_existing_report.get("production_id", production_id)
                report["workorderId"] = retry_existing_report.get("workorder_id", workorder_id)
                report["idempotencyKey"] = retry_existing_report.get("idempotency_key", idempotency_key)
                report["odooReportId"] = retry_existing_report.get("odoo_report_id", "")
                report["odooStockMoveIds"] = retry_existing_report.get("odoo_stock_move_ids", "")
                retry_material_status = retry_existing_report.get("material_sync_status", "unknown")
                if retry_material_status == "unknown" and old_status == "odoo_partial":
                    # Rows written before material_sync_status was introduced
                    # used partial specifically for "stock done, progress
                    # failed".  Preserve that retry behavior for legacy rows.
                    retry_material_status = "synced"
                retry_material_required = retry_material_status not in ("not_required", "")
                stored_progress_qty = retry_existing_report.get("odoo_progress_qty")
                try:
                    if stored_progress_qty not in (None, ""):
                        retry_progress_target = float(stored_progress_qty)
                except (TypeError, ValueError):
                    retry_progress_target = None
                # The original material list remains in report_materials for
                # audit purposes, but must never be sent to Odoo again.
                materials = []

            # 兼容旧格式: orderId 用 report UUID 避免唯一约束冲突
            if not report.get("orderId") or report.get("orderId") == "":
                report["orderId"] = f"direct-{report['id'][:8]}"

            # === Mock 模式 ===
            if mode == "mock":
                if db_add_report(report, materials):
                    saved = db_get_report(report["id"]) or report
                    result = _normalize_report(saved) if isinstance(saved, dict) else saved
                    _push_report_admin(result, db_get_report_materials(report["id"]), final_status=True)
                    self.write_json({
                        "ok": True,
                        "data": result,
                        "meta": {
                            "mode": "mock",
                            "source": "fake_odoo",
                            "message": "模拟报工成功，未写入 Odoo",
                        },
                    })
                else:
                    self.write_json({
                        "ok": False,
                        "error": "重复请求：该幂等键已被使用"
                    }, status=HTTPStatus.CONFLICT)
                return

            # === 真实模式 - Odoo 写入 ===
            # Persist before touching Odoo. If the process stops during an
            # external call, the report remains visible as odoo_pending and an
            # identical retry is blocked instead of deducting stock twice.
            if not retry_existing_report:
                report["syncStatus"] = "odoo_pending"
                report["materialSyncStatus"] = "pending" if materials else "not_required"
                report["errorMessage"] = "Odoo 同步处理中"
                if not db_add_report(report, materials):
                    self.write_json({
                        "ok": False,
                        "error": "重复请求：该幂等键已被使用",
                    }, status=HTTPStatus.CONFLICT)
                    return
                _push_report_admin(report, materials)

            # 调用 Odoo 物料扣减（不依赖工单）
            odoo_result = None
            if materials and not MOCK_MODE:
                try:
                    odoo_result = odoo_deduct_materials(
                        materials=materials,
                        production_id=int(production_id) if str(production_id).isdigit() else None,
                        qty=qty,
                        idempotency_key=idempotency_key,
                    )
                    logger.info(f"物料扣减结果: {odoo_result.get('message','')}")
                except Exception as e:
                    logger.error(f"物料扣减异常: {e}")
                    odoo_result = {"ok": False, "error": str(e)}
            elif retry_existing_report:
                odoo_result = {
                    "ok": retry_material_status in ("not_required", "synced"),
                    "skipped": True,
                    "message": "重试仅同步工单进度，未重复扣减物料",
                }

            # 报工后同步 Odoo 中 MO/工单的 qty_produced + 状态
            progress_result = None
            if not MOCK_MODE and workorder_id:
                try:
                    client = get_odoo()
                    progress_result = odoo_update_workorder_progress(
                        client=client,
                        workorder_id=int(workorder_id) if str(workorder_id).isdigit() else 0,
                        qty=qty,
                        production_id=int(production_id) if str(production_id).isdigit() else 0,
                        **({"target_qty": retry_progress_target}
                           if retry_progress_target is not None else {}),
                    )
                    logger.info(f"Odoo 进度同步: {progress_result}")
                except Exception as e:
                    logger.warning(f"Odoo 进度同步异常: {e}")
                    progress_result = {"ok": False, "error": str(e)}

            compensation_result = None
            if (not retry_existing_report and materials and odoo_result and odoo_result.get("ok")
                    and not (progress_result and progress_result.get("ok"))):
                try:
                    compensation_result = compensate_material_deduction(
                        get_odoo(), materials, int(production_id)
                    )
                    logger.warning(
                        f"工单进度失败，已补偿本次物料扣减: {compensation_result}"
                    )
                except Exception as compensation_error:
                    compensation_result = {
                        "ok": False,
                        "error": str(compensation_error),
                    }
                    logger.error(f"物料自动补偿失败: {compensation_error}")

            # 只有所有需要写入 Odoo 的部分都成功，才能标记为完整同步。
            # 之前只要物料扣减成功就写成 odoo_synced，导致“库存已变、MO/WO 未变”
            # 被面板误显示为成功。
            report["odooReportId"] = str(workorder_id)
            report["odooStockMoveIds"] = json.dumps(
                (odoo_result or {}).get("stock_move_ids", []), ensure_ascii=False
            )
            if retry_existing_report:
                # Preserve the original material outcome while retrying only
                # the progress side.  A partial/failed material sync can never
                # be promoted to fully synced by a progress-only retry.
                report["odooStockMoveIds"] = retry_existing_report.get("odoo_stock_move_ids", "")
                report["materialSyncStatus"] = retry_material_status
            elif not materials:
                report["materialSyncStatus"] = "not_required"
            elif compensation_result and compensation_result.get("ok"):
                report["materialSyncStatus"] = "compensated"
            elif odoo_result and odoo_result.get("partial"):
                report["materialSyncStatus"] = "partial"
            elif odoo_result and odoo_result.get("ok"):
                report["materialSyncStatus"] = "synced"
            else:
                report["materialSyncStatus"] = "failed"

            material_required = bool(materials) or retry_material_required
            if retry_existing_report:
                material_ok = retry_material_status in ("not_required", "synced")
                material_partial = retry_material_status == "partial"
                material_write_ok = retry_material_status in ("synced", "partial")
            else:
                material_ok = (
                    (not material_required)
                    or bool(odoo_result and odoo_result.get("ok"))
                    or bool(compensation_result and compensation_result.get("ok"))
                )
                material_partial = bool(odoo_result and odoo_result.get("partial"))
                material_write_ok = bool(
                    odoo_result and odoo_result.get("ok")
                    and not (compensation_result and compensation_result.get("ok"))
                )
            progress_ok = bool(progress_result and progress_result.get("ok"))
            # 历史兼容：旧客户端可能在没有 workorder/production ID 时把物料写入 Odoo。
            # 新请求已在上方拒绝这种数据；这里即使被旧进程处理，也不能再标记为完整同步。
            if not str(workorder_id).isdigit() or not str(production_id).isdigit():
                progress_ok = False
                errors_list = ["缺少有效的工单/生产订单 ID，无法同步制造订单进度"]
            else:
                errors_list = []
            if material_required and not material_ok:
                if retry_existing_report:
                    material_errors = [
                        retry_existing_report.get("error_message") or "物料同步尚未完成，请先处理物料后再重试"
                    ]
                else:
                    material_errors = (odoo_result or {}).get("errors") or [(odoo_result or {}).get("error", "物料库存扣减失败")]
                errors_list.extend(material_errors if isinstance(material_errors, list) else [material_errors])
            elif material_partial:
                if retry_existing_report:
                    partial_errors = [
                        retry_existing_report.get("error_message") or "部分物料库存扣减失败"
                    ]
                else:
                    partial_errors = odoo_result.get("errors") or ["部分物料库存扣减失败"]
                errors_list.extend(partial_errors if isinstance(partial_errors, list) else [partial_errors])
            if not progress_ok:
                progress_error = (progress_result or {}).get("error", "制造订单/工序进度同步失败")
                errors_list.append(progress_error)
            if compensation_result and not compensation_result.get("ok"):
                errors_list.append(
                    "工单同步失败且物料自动补偿失败: "
                    + compensation_result.get("error", "未知错误")
                )

            if material_ok and progress_ok and not material_partial:
                report["syncStatus"] = "odoo_synced"
                report["errorMessage"] = ""
            elif progress_ok or (material_required and material_write_ok):
                # 任一 Odoo 子步骤已成功但另一子步骤失败，都属于部分同步。
                # 例如：库存已经扣减、但 WO/MO 回读失败，不能标成“未同步”。
                report["syncStatus"] = "odoo_partial"
                report["errorMessage"] = "; ".join(str(e) for e in errors_list)
            else:
                report["syncStatus"] = "odoo_failed"
                report["errorMessage"] = "; ".join(str(e) for e in errors_list) or "Odoo 同步失败"

            progress_qty = (progress_result or {}).get("new_qty")
            if progress_qty is not None:
                report["odooProgressQty"] = progress_qty
            elif retry_existing_report:
                report["odooProgressQty"] = retry_existing_report.get("odoo_progress_qty")

            # 任一 Odoo 写入成功后都失效相关缓存，避免面板显示旧数据。
            if material_ok or progress_ok:
                _invalidate_runtime_caches()

            saved_ok = db_update_report_sync(
                report_id=report["id"],
                sync_status=report["syncStatus"],
                error_message=report["errorMessage"],
                odoo_report_id=report.get("odooReportId"),
                odoo_stock_move_ids=report.get("odooStockMoveIds"),
                material_sync_status=report.get("materialSyncStatus"),
                odoo_progress_qty=report.get("odooProgressQty"),
            )

            if saved_ok:
                saved = db_get_report(report["id"]) or report
                result = _normalize_report(saved) if isinstance(saved, dict) else saved
                _push_report_admin(result, db_get_report_materials(report["id"]), final_status=True)

                if report["syncStatus"] == "odoo_synced":
                    msg = "报工成功，Odoo 物料库存与制造订单进度已同步"
                elif report["syncStatus"] == "odoo_partial":
                    msg = "报工已保存，但 Odoo 仅部分同步：" + report["errorMessage"]
                else:
                    msg = "报工已保存，但 Odoo 未完成同步：" + report["errorMessage"]

                self.write_json({
                    "ok": True,
                    "data": result,
                    "meta": {
                        "mode": "real",
                        "source": "odoo",
                        "message": msg,
                        "odoo_result": odoo_result,
                        "progress_result": progress_result,
                        "syncStatus": report["syncStatus"],
                        "errorMessage": report["errorMessage"],
                    },
                })
            else:
                self.write_json({
                    "ok": False,
                    "error": "本地报工已保留，但同步状态更新失败；请勿重复提交并联系管理员"
                }, status=HTTPStatus.INTERNAL_SERVER_ERROR)

        except json.JSONDecodeError:
            self.write_json({"ok": False, "error": "无效的 JSON 格式"}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            logger.error(f"handle_report_post 异常: {exc}", exc_info=True)
            self.write_json({"ok": False, "error": f"服务器错误: {exc}"},
                            status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def handle_worker_sync_post(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            worker = json.loads(body)
            if not isinstance(worker, dict):
                raise ValueError("员工数据必须是对象")
            wid = str(worker.get("sourceWorkerId", "")).strip()
            name = str(worker.get("name", "")).strip()
            team = str(worker.get("departmentName", worker.get("team", ""))).strip()
            operation_codes = worker.get("operationCodes", [])
            operation_bindings = worker.get("operationBindings", [])
            job_roles = worker.get("jobRoles", worker.get("roles", []))
            if not wid or not name or not team:
                self.write_json({"ok": False, "error": "工人编号、姓名和所属部门不能为空"},
                                status=HTTPStatus.BAD_REQUEST)
                return
            if not isinstance(operation_codes, list):
                operation_codes = []
            if not operation_codes and isinstance(job_roles, list):
                operation_codes = [
                    str(op.get("code", ""))
                    for role in job_roles if isinstance(role, dict)
                    for op in (role.get("operations", []) if isinstance(role.get("operations", []), list) else [])
                    if isinstance(op, dict) and op.get("code")
                ]
            if not operation_codes:
                self.write_json({"ok": False, "error": "工作岗位未匹配到工序"}, status=HTTPStatus.BAD_REQUEST)
                return
            operation_codes = [str(code) for code in operation_codes]
            binding_codes = {
                str(binding.get("code", "")) for binding in operation_bindings
                if isinstance(binding, dict)
            }
            if any(code not in VALID_OPERATIONS and code not in binding_codes for code in operation_codes):
                self.write_json({"ok": False, "error": "包含无效工序绑定"}, status=HTTPStatus.BAD_REQUEST)
                return
            source = _effective_worker_source(wid, "report_admin")
            db_upsert_worker(wid, name, team, source, operation_codes, job_roles)
            with _WORKER_CACHE_LOCK:
                _WORKER_CACHE["data"] = None
                _WORKER_CACHE["ts"] = 0
            self.write_json({"ok": True, "data": {
                "id": wid, "name": name, "team": team, "source": source,
                "odooEmployeeId": 0, "operationCodes": operation_codes,
                "operationBindings": operation_bindings if isinstance(operation_bindings, list) else [],
                "jobRoles": job_roles if isinstance(job_roles, list) else [],
            }})
        except (ValueError, json.JSONDecodeError) as exc:
            self.write_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            logger.error(f"handle_worker_sync_post 异常: {exc}", exc_info=True)
            self.write_json({"ok": False, "error": f"服务器错误: {exc}"},
                            status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def handle_worker_post(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            worker = json.loads(body)
            if not worker.get("name", "").strip():
                self.write_json({"ok": False, "error": "工人姓名不能为空"}, status=HTTPStatus.BAD_REQUEST)
                return
            wid = worker.get("id", "").strip() or f"WK{uuid.uuid4().hex[:3].upper()}"
            name = worker["name"].strip()
            team = worker.get("team", "").strip()
            source = worker.get("source", "local")
            odoo_eid = worker.get("odooEmployeeId", 0)
            operation_codes = worker.get("operationCodes", [])
            if not isinstance(operation_codes, list):
                operation_codes = []
            unknown_ops = [code for code in operation_codes if code not in VALID_OPERATIONS]
            if unknown_ops:
                self.write_json({"ok": False, "error": "包含无效工序绑定"},
                                status=HTTPStatus.BAD_REQUEST)
                return
            existing = get_valid_worker_ids()
            if wid in existing:
                self.write_json({"ok": False, "error": f"工号 {wid} 已存在"}, status=HTTPStatus.CONFLICT)
                return
            db_add_worker(wid, name, team, source, odoo_eid, operation_codes)
            self.write_json({"ok": True, "data": {"id": wid, "name": name, "team": team,
                                                   "source": source, "odooEmployeeId": odoo_eid,
                                                   "operationCodes": operation_codes}})
        except json.JSONDecodeError:
            self.write_json({"ok": False, "error": "无效的 JSON 格式"}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            logger.error(f"handle_worker_post 异常: {exc}", exc_info=True)
            self.write_json({"ok": False, "error": f"服务器错误: {exc}"},
                            status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def dashboard_payload(self, panel_worker=None):
        try:
            # The legacy dashboard is not employee-scoped. Keep its shape for
            # the existing client while avoiding cross-worker order leakage.
            return {"ok": True, "data": {"deliveryRows": []}}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def report_stats_payload(self, panel_worker=None):
        try:
            today = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
            reports = db_reports(date_filter=today)
            if panel_worker is not None:
                reports = [
                    report for report in reports
                    if str(report.get("worker_id", "")) == str(panel_worker["id"])
                ]
            total_qty = sum(int(r.get("qty", 0)) for r in reports)
            total_hours = sum(float(r.get("hours", 0)) for r in reports)
            unique_workers = len({r.get("worker_name") for r in reports})
            mode = get_odoo_mode()
            completed_qty = 0
            display_reports = reports
            if mode == "real":
                completed_qty = completed_machine_qty_for_reports(reports)
                display_reports = reports + odoo_today_progress_snapshots(reports)
            return {
                "ok": True,
                "data": {
                    "todayCount": len(display_reports),
                    "todayOutput": completed_qty,
                    "todayHours": round(total_hours, 1),
                    "activeWorkers": unique_workers,
                    "recentReports": [{
                        "workerName": r["worker_name"], "workerTeam": r.get("worker_team", ""),
                        "orderId": r["order_id"], "qty": r["qty"],
                        "operationLabel": r["operation_label"], "operation": r["operation"],
                        "hours": r["hours"], "time": r["time"],
                    } for r in reports[:8]],
                },
                "meta": {"mode": mode, "source": "odoo" if mode == "real" else "mock"},
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def write_json(self, payload, status=HTTPStatus.OK, headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        # API data includes live Odoo and local report state. Prevent browsers
        # from displaying a pre-reset response while the page remains open.
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)


# ============================================================
# 主入口
# ============================================================

_running = True

def graceful_shutdown(signum, frame):
    global _running
    logger.info(f"收到信号 {signum}，准备关闭...")
    _running = False


def main():
    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)

    _init_db()
    _seed_workers()
    # Mock 切 Real 时自动从 mock DB 迁移数据
    if not MOCK_MODE:
        mock_db = BASE_DIR / "data.mock.db"
        if mock_db.exists():
            try:
                with sqlite3.connect(str(mock_db)) as ms:
                    reports = ms.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
                if reports > 0:
                    logger.info(f"检测到 mock 数据库有 {reports} 条记录，可手动迁移: {mock_db}")
            except Exception:
                pass

    port = int(os.getenv("PORT", "8090"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.timeout = 2
    logger.info(f"生产员工报工系统: http://0.0.0.0:{port}")
    logger.info(f"Odoo: {ODOO_URL}")
    logger.debug(f"Odoo 连接详情: db={ODOO_DB} user={ODOO_USER}")
    logger.info(f"模式: {'模拟 (MOCK)' if MOCK_MODE else '真实 (REAL)'}")
    logger.info(f"数据库: {DB_FILE}")
    logger.info(f"Auth: {'已启用' if API_KEY else '未启用（POST 接口无保护）'}")

    try:
        while _running:
            server.handle_request()
    except KeyboardInterrupt:
        pass
    logger.info("服务器已关闭")


if __name__ == "__main__":
    main()
