import json
import logging
import os
import re
import signal
import sqlite3
import threading
import time
import uuid
import xmlrpc.client
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

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
DB_FILE = BASE_DIR / ("data.mock.db" if MOCK_MODE else "data.db")
WHITE_EXT = {".html", ".css", ".js", ".svg", ".ico", ".png"}

# ============================================================
# 日志
# ============================================================

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("production-dashboard")
if MOCK_MODE:
    logger.warning("=" * 60)
    logger.warning("ODOO MOCK MODE ENABLED - NO REAL ODOO WRITES")
    logger.warning("=" * 60)
else:
    logger.info(f"Odoo: {ODOO_URL} db={ODOO_DB} user={ODOO_USER}")

# ============================================================
# 并发锁
# ============================================================

DB_LOCK = threading.Lock()
ODOO_LOCK = threading.Lock()

# ============================================================
# Odoo 客户端
# ============================================================

class OdooError(RuntimeError):
    pass


class OdooClient:
    def __init__(self):
        self._uid = None
        # 设置 30 秒超时避免无限等待
        import socket
        socket.setdefaulttimeout(30)
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
        if MOCK_MODE:
            from fake_odoo_client import FakeOdooClient
            _odoo_client = FakeOdooClient()
            _odoo_mode = "mock"
            logger.warning("FakeOdooClient 已激活 - 模拟模式")
        else:
            _odoo_client = OdooClient()
            _odoo_mode = "real"
    return _odoo_client

def get_odoo_mode():
    global _odoo_mode
    if _odoo_mode is None:
        get_odoo()
    return _odoo_mode or ("mock" if MOCK_MODE else "real")

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
            ("error_message", "TEXT DEFAULT ''"),
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
                error_message TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_reports_date ON reports(date);
            CREATE INDEX IF NOT EXISTS idx_reports_worker_date ON reports(worker_id, date);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_reports_worker_date
                ON reports(worker_id, order_id, date, operation);
        """)
        conn.commit()
        conn.close()
    logger.info("SQLite 数据库初始化完成")
    # 执行迁移
    _migrate_db()


def _seed_workers():
    """首次启动时写入默认工人（含罗伟华）"""
    with DB_LOCK:
        conn = sqlite3.connect(str(DB_FILE))
        count = conn.execute("SELECT COUNT(*) FROM workers").fetchone()[0]
        if count == 0:
            default = [
                ("WK001", "张建国", "A班", "local", 0),
                ("WK002", "��明辉", "A班", "local", 0),
                ("WK003", "王志强", "B班", "local", 0),
                ("WK004", "陈晓峰", "B班", "local", 0),
                ("WK005", "刘大伟", "C班", "local", 0),
                ("WK006", "赵永刚", "夜班", "local", 0),
                ("LOCAL_LWH", "罗伟华", "组装班", "local", 0),
            ]
            conn.executemany(
                "INSERT INTO workers (id, name, team, source, odoo_employee_id) VALUES (?, ?, ?, ?, ?)",
                default
            )
            conn.commit()
            logger.info("已写入 7 个默认工人（含罗伟华）")
        else:
            # 确保罗伟华存在（如果没有的话）
            existing = conn.execute("SELECT id FROM workers WHERE name = '罗伟华'").fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO workers (id, name, team, source, odoo_employee_id) VALUES (?, ?, ?, ?, ?)",
                    ("LOCAL_LWH", "罗伟华", "组装班", "local", 0)
                )
                conn.commit()
                logger.info("已添加罗伟华（本地工人）")
        conn.close()


def db_workers():
    with DB_LOCK:
        c = sqlite3.connect(str(DB_FILE))
        c.row_factory = sqlite3.Row
        rows = c.execute("SELECT id, name, team, source, odoo_employee_id FROM workers ORDER BY id").fetchall()
        c.close()
    results = []
    for r in rows:
        w = {"id": r["id"], "name": r["name"], "team": r["team"],
             "source": r["source"] if "source" in r.keys() else "local",
             "odooEmployeeId": r["odoo_employee_id"] if "odoo_employee_id" in r.keys() else 0}
        results.append(w)
    return results


def db_add_worker(wid, name, team, source="local", odoo_employee_id=0):
    with DB_LOCK:
        c = sqlite3.connect(str(DB_FILE))
        c.execute(
            "INSERT INTO workers (id, name, team, source, odoo_employee_id) VALUES (?, ?, ?, ?, ?)",
            (wid, name, team, source, odoo_employee_id)
        )
        c.commit()
        c.close()
    logger.info(f"添加工人: {name} ({wid}), source={source}")


def _normalize_report(row):
    """将 SQLite 字段转为前端格式"""
    base = {
        "id": row["id"], "workerId": row["worker_id"], "workerName": row["worker_name"],
        "workerTeam": row.get("worker_team", ""),
        "orderId": row["order_id"], "orderCustomer": row.get("order_customer", ""),
        "orderProduct": row.get("order_product", ""),
        "operation": row["operation"], "operationLabel": row["operation_label"],
        "qty": row["qty"], "qualified": row["qualified"], "hours": row["hours"],
        "remark": row.get("remark", ""), "date": row["date"], "time": row["time"],
        "timestamp": row["timestamp"],
    }
    # 新字段
    for f in ["production_id", "workorder_id", "odoo_employee_id", "idempotency_key",
              "odoo_report_id", "sync_status"]:
        if f in row.keys():
            base[f] = row[f]
    return base


REPORT_COLS = ["id", "worker_id", "worker_name", "worker_team", "order_id",
               "order_customer", "order_product", "operation", "operation_label",
               "qty", "qualified", "hours", "remark", "date", "time", "timestamp",
               "production_id", "workorder_id", "odoo_employee_id",
               "idempotency_key", "odoo_report_id", "odoo_stock_move_ids",
               "sync_status", "error_message", "created_at"]


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
                 odoo_stock_move_ids, sync_status, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                 report.get("syncStatus", "local"), report.get("errorMessage", "")),
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

def load_workers():
    return db_workers()


def load_reports():
    return db_reports()


# ============================================================
# 工序定义
# ============================================================

OPERATIONS = [
    {"id": "assembly", "code": "assembly", "name": "总装", "hostType": None},
    {"id": "testing", "code": "testing", "name": "测试", "hostType": None},
    {"id": "qc", "code": "qc", "name": "质检", "hostType": None},
    {"id": "packing", "code": "packing", "name": "包装", "hostType": None},
    {"id": "debug", "code": "debug", "name": "调试", "hostType": None},
    {"id": "pc_assembly_tape", "code": "pc_assembly_tape", "name": "电脑装机（编带主机）", "hostType": "tape",
     "odooWorkcenterId": 101, "odooWorkcenterCode": "pc_assembly_tape"},
    {"id": "pc_assembly_splitter", "code": "pc_assembly_splitter", "name": "电脑装机（分光主机）", "hostType": "splitter",
     "odooWorkcenterId": 102, "odooWorkcenterCode": "pc_assembly_splitter"},
]

VALID_OPERATIONS = {op["code"] for op in OPERATIONS}
OPERATION_MAP = {op["code"]: op for op in OPERATIONS}


def get_operations():
    """返回完整工序列表"""
    mode = get_odoo_mode()
    ops = []
    for op in OPERATIONS:
        o = dict(op)
        o["meta"] = {"mode": mode, "source": "odoo" if mode == "real" else "mock"}
        ops.append(o)
    return ops


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


_worker_ids_lock = threading.Lock()

def get_valid_worker_ids():
    with _worker_ids_lock:
        workers = db_workers()
        return {w["id"] for w in workers}


_order_ids_cache = {"ids": set(), "ts": 0}
_ORDER_CACHE_TTL = 60
_order_ids_lock = threading.Lock()

def get_valid_order_ids():
    now = time.time()
    with _order_ids_lock:
        if now - _order_ids_cache["ts"] < _ORDER_CACHE_TTL and _order_ids_cache["ids"]:
            return _order_ids_cache["ids"]
    try:
        data = load_dashboard()
        ids = {row["order"] for row in data.get("deliveryRows", [])}
        with _order_ids_lock:
            _order_ids_cache["ids"] = ids
            _order_ids_cache["ts"] = time.time()
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


def bracket_code(value):
    match = re.match(r"^\[([^\]]+)\]", value or "")
    return match.group(1) if match else value or "-"


def number(value):
    return float(value or 0)


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
_DASH_CACHE = {"data": None, "ts": 0}
_DASH_CACHE_TTL = 300


def load_dashboard():
    """加载Dashboard数据（30秒缓存，减少Odoo重复查询）"""
    now = time.time()
    if _DASH_CACHE["data"] is not None and (now - _DASH_CACHE["ts"]) < _DASH_CACHE_TTL:
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
    _DASH_CACHE["data"] = result
    _DASH_CACHE["ts"] = time.time()
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
_BOM_CACHE = {"data": None, "ts": 0, "key": None}
_BOM_CACHE_LOCK = threading.Lock()
_BOM_CACHE_TTL = 300


def get_bom_data(host_type):
    """获取 BOM 数据（缓存 + 线程安全）"""
    if host_type not in ("tape", "splitter"):
        return []

    now = time.time()
    cache_key = f"{get_odoo_mode()}:{host_type}"
    with _BOM_CACHE_LOCK:
        if _BOM_CACHE["key"] == cache_key and _BOM_CACHE["data"] is not None and (now - _BOM_CACHE["ts"]) < _BOM_CACHE_TTL:
            logger.info(f"BOM缓存命中 [{host_type}]")
            return _BOM_CACHE["data"]

    codes = TAPE_BOM_CODES if host_type == "tape" else SPLITTER_BOM_CODES
    excel_items = EXCEL_BOM.get(host_type, [])
    bom_line_ids = MOCK_BOM_LINE_IDS.get(host_type, {})
    mode = get_odoo_mode()

    client = get_odoo() if mode == "real" else None

    # 一次性查出所有 product.product，避免循环查询
    products_by_id = {}  # {product_id: {code, name, categ, tmpl_id, seller_ids}}
    if mode == "real" and client:
        try:
            rows = client.search_read(
                "product.product",
                [("default_code", "in", codes)],
                ["id", "default_code", "name", "product_tmpl_id", "categ_id", "uom_id", "seller_ids"],
                limit=20
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
    for i, code in enumerate(codes):
        excel = excel_items[i] if i < len(excel_items) else {}

        # 从查询结果中找匹配 code 的 product
        matched_product = None
        for pid, pdata in products_by_id.items():
            if pdata["code"] == code:
                matched_product = (pid, pdata)
                break

        # 默认值（Odoo 读不到时回退到 Excel）
        product_name = excel.get("name", "")
        spec = excel.get("spec", "")
        # 类别固定显示 Excel 中的"主机配件"，不读 Odoo categ_id
        category_name = excel.get("category", "主机配件")
        # 供应商优先读 Odoo 的 seller_ids，Odoo 没有时回退到 Excel
        brand_name = supplier_name_by_pid.get(code, excel.get("brand", ""))
        pid = 0
        tmpl_id = 0

        if matched_product:
            pid, pdata = matched_product
            tmpl_id = pdata.get("tmpl_id") or 0
            product_name = pdata.get("name", "") or product_name
            # 模板数据（仅规格）
            if tmpl_id and tmpl_id in templates_by_id:
                spec = templates_by_id[tmpl_id].get("spec", "") or spec

        # 清理供应商名称（去掉 [P00202] 前缀，只保留"淘宝电商公司"或类似纯名称）
        if brand_name and brand_name.startswith("["):
            # 格式: [P00202] 淘宝电商公司 → 淘宝电商公司
            m = re.match(r"^\[[^\]]+\]\s*(.+)$", brand_name)
            if m:
                brand_full = m.group(1).strip()
                # 简化: 淘宝电商公司 → 淘宝
                if "淘宝" in brand_full:
                    brand_name = "淘宝"
                else:
                    brand_name = brand_full

        # 获取库存（从预先批量查询的 stock_by_pid 中取值）
        available_qty = stock_by_pid.get(pid, 0)

        bom_line_id = bom_line_ids.get(code, 0)

        item = {
            "bomLineId": bom_line_id,
            "productId": pid,
            "productTemplateId": tmpl_id,
            "defaultCode": code,
            "name": product_name,
            "specification": spec,
            "uomId": 1,
            "uomName": "pcs",
            "bomQty": excel.get("qty", 1),
            "categoryName": category_name,
            "brandSupplierName": brand_name,
            "availableQty": available_qty,
            "selected": True,
            "actualQty": excel.get("qty", 1),
            "meta": {"mode": mode, "source": "odoo" if mode == "real" else "mock"},
        }
        items.append(item)

    # 写入缓存（线程安全）
    with _BOM_CACHE_LOCK:
        _BOM_CACHE["data"] = items
        _BOM_CACHE["ts"] = time.time()
        _BOM_CACHE["key"] = cache_key
    return items


_WO_CACHE = {"data": None, "ts": 0}
_WO_CACHE_TTL = 300


def get_workorders_data():
    """
    获取工单列表（30秒缓存）
    从 Odoo mrp.workorder 读取（非 sale.order）
    """
    now = time.time()
    if _WO_CACHE["data"] is not None and (now - _WO_CACHE["ts"]) < _WO_CACHE_TTL:
        return _WO_CACHE["data"]
    mode = get_odoo_mode()
    try:
        client = get_odoo()
        wo_fields = ["id", "name", "production_id", "workcenter_id", "operation_id",
                     "product_id", "state", "qty_production", "qty_produced",
                     "qty_remaining", "duration_expected"]
        wo_rows = client.search_read(
            "mrp.workorder",
            [("state", "not in", ["done", "cancel"])],
            wo_fields, limit=50, order="id desc"
        )

        # 获取生产单信息
        mo_ids = set()
        for wo in wo_rows:
            pid = rel_id(wo.get("production_id"))
            if pid:
                mo_ids.add(pid)

        mo_data = {}
        if mo_ids:
            mo_rows = client.read("mrp.production", list(mo_ids),
                                  ["id", "name", "product_id", "product_qty", "state", "origin"])
            for mo in mo_rows:
                mo_data[mo["id"]] = mo

        workorders = []
        # 状态翻译
        WO_STATE_MAP = {
            "draft": "草稿", "pending": "待处理", "ready": "就绪",
            "waiting": "等待中", "progress": "生产中", "done": "完成",
            "cancel": "已取消", "to_close": "待关闭",
        }
        for wo in wo_rows:
            mo_id = rel_id(wo.get("production_id"))
            mo = mo_data.get(mo_id, {})
            pid = rel_id(wo.get("product_id"))

            # 确定主机类型
            host_type = None
            if pid == 11632:
                host_type = "tape"
            elif pid == 11633:
                host_type = "splitter"

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
            })
        _WO_CACHE["data"] = workorders
        _WO_CACHE["ts"] = time.time()
        return workorders
    except Exception as e:
        logger.warning(f"获取工单失败: {e}")
        raise


# ============================================================
# HTTP Handler
# ============================================================

# ============================================================
# Odoo 报工 + 库存扣减
# ============================================================

# 原材料来源库位（内部库存）
SRC_LOCATION_ID = 8       # WH/库存
# 生产消耗���位（虚拟生产库位）
DEST_LOCATION_ID = 15     # Virtual Locations/Production


def odoo_deduct_materials(materials, idempotency_key):
    """直接扣减物料库存（启用负库存 -> 调 quant -> 创建 move 记录）"""
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
    stock_move_ids = []
    errors = []

    for mat in materials:
        product_id = mat.get("productId", 0)
        actual_qty = mat.get("actualQty", 1)
        uom_id = mat.get("uomId", 1)
        code = mat.get("defaultCode", "?")

        if not product_id or actual_qty <= 0:
            errors.append(f"物料 {code}: 无效参数")
            continue

        try:
            _ensure_negative_stock_ok(client, product_id)

            move_id = client.call("stock.move", "create", [{
                "product_id": product_id, "product_uom_qty": actual_qty,
                "product_uom": uom_id, "location_id": SRC_LOCATION_ID,
                "location_dest_id": DEST_LOCATION_ID,
                "name": f"报工消耗 {code} [{idempotency_key[:8]}]", "state": "draft",
            }])
            logger.info(f"物料 {code}({product_id}): stock.move#{move_id} 已创建 qty={actual_qty}")

            remaining = actual_qty
            quant_ids = client.call("stock.quant", "search", [
                [("product_id", "=", product_id), ("location_id", "=", SRC_LOCATION_ID)]
            ])
            if quant_ids:
                quants = client.call("stock.quant", "read", [quant_ids], {"fields": ["id", "quantity"]})
                for q in quants:
                    if remaining <= 0:
                        break
                    qty = float(q["quantity"])
                    take = min(qty, remaining)
                    client.call("stock.quant", "write", [[q["id"]], {
                        "quantity": qty - take, "inventory_quantity": qty - take,
                    }])
                    remaining -= take
                    logger.info(f"物料 {code}: quant#{q['id']} {qty} -> {qty - take}")

            if remaining > 0:
                if quant_ids:
                    last = quants[-1]
                    client.call("stock.quant", "write", [[last["id"]], {
                        "quantity": float(last["quantity"]) - remaining,
                        "inventory_quantity": float(last["quantity"]) - remaining,
                    }])
                else:
                    client.call("stock.quant", "create", [{
                        "product_id": product_id, "location_id": SRC_LOCATION_ID,
                        "quantity": -actual_qty, "inventory_quantity": -actual_qty,
                    }])
                logger.info(f"物料 {code}: 超额/负库存扣减 {remaining}")

            try:
                client.call("stock.move", "write", [[move_id], {"state": "done", "quantity": actual_qty}])
            except Exception:
                pass

            # quant 扣减成功后才计入成功列表
            stock_move_ids.append(move_id)

        except Exception as e:
            errors.append(f"物料 {code}: {e}")
            logger.error(f"物料 {code} 扣减失败: {e}")

    real_errors = [e for e in errors if "无效" not in e]
    all_failed = len(materials) > 0 and len(stock_move_ids) == 0 and len(real_errors) > 0
    partial = len(real_errors) > 0 and len(stock_move_ids) > 0

    if all_failed:
        return {"ok": False, "stock_move_ids": [], "errors": errors,
                "message": f"全部 {len(real_errors)} 项物料扣减失败",
                "meta": {"mode": "real", "source": "odoo"}}
    if partial:
        return {"ok": True, "partial": True, "stock_move_ids": stock_move_ids, "errors": errors,
                "message": f"{len(stock_move_ids)} 项已扣减，{len(real_errors)} 项失败",
                "meta": {"mode": "real", "source": "odoo"}}
    return {"ok": True, "stock_move_ids": stock_move_ids,
            "message": f"已完成 {len(stock_move_ids)} 项物料库存扣减",
            "meta": {"mode": "real", "source": "odoo"}}


def _ensure_negative_stock_ok(client, product_id):
    """为产品模板启用负库存"""
    if not hasattr(_ensure_negative_stock_ok, "_done"):
        _ensure_negative_stock_ok._done = set()
    if product_id in _ensure_negative_stock_ok._done:
        return
    try:
        rows = client.call("product.product", "search_read", [[("id", "=", product_id)]],
                           {"fields": ["product_tmpl_id"], "limit": 1})
        if rows:
            tmpl = rows[0].get("product_tmpl_id", 0)
            if isinstance(tmpl, (list, tuple)):
                tmpl = tmpl[0]
            client.call("product.template", "write", [[tmpl], {"allow_negative_stock": True}])
            _ensure_negative_stock_ok._done.add(product_id)
            logger.info(f"物料 #{product_id} tmpl#{tmpl} 负库存已启用")
    except Exception:
        pass



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
            return self._route_get_api(path, params)
        ext = os.path.splitext(path)[1].lower()
        if path == "/" or ext in WHITE_EXT:
            return super().do_GET()
        self.send_error(HTTPStatus.NOT_FOUND)

    def _route_get_api(self, path, params):
        if path == "/api/dashboard":
            self.write_json(self.dashboard_payload())
        elif path == "/api/health":
            self.write_json({"ok": True, "mode": get_odoo_mode()})
        elif path == "/api/workers":
            workers = load_workers()
            self.write_json({"ok": True, "data": workers,
                             "meta": {"mode": get_odoo_mode(), "count": len(workers)}})
        elif path == "/api/reports":
            reports = load_reports()
            self.write_json({"ok": True, "data": [_normalize_report(r) for r in reports]})
        elif path == "/api/order-summary":
            self.write_json(self.order_summary_payload())
        elif path == "/api/report-stats":
            self.write_json(self.report_stats_payload())
        elif path == "/api/operations":
            ops = get_operations()
            self.write_json({"ok": True, "data": ops,
                             "meta": {"mode": get_odoo_mode(), "count": len(ops)}})
        elif path == "/api/workorders":
            try:
                wos = get_workorders_data()
                self.write_json({"ok": True, "data": wos,
                                 "meta": {"mode": get_odoo_mode(), "count": len(wos)}})
            except Exception as e:
                self.write_json({"ok": False, "error": f"获取工单失败: {e}"},
                                status=HTTPStatus.INTERNAL_SERVER_ERROR)
        elif path == "/api/bom":
            host_type = params.get("hostType", params.get("host_type", ""))
            workorder_id = params.get("workorderId", params.get("workorder_id", ""))
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
            items = get_bom_data(host_type)
            self.write_json({"ok": True, "data": items,
                             "meta": {"mode": get_odoo_mode(), "hostType": host_type,
                                      "count": len(items)}})
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        path = urlparse(self.path).path
        # 请求体大小限制 64KB
        length = int(self.headers.get("Content-Length", 0))
        if length > 65536:
            self.send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Body too large")
            return
        if not check_auth(self):
            self.write_json({"ok": False, "error": "未授权：缺少或无效的 API Key"},
                            status=HTTPStatus.UNAUTHORIZED)
            return
        if path == "/api/reports":
            self.handle_report_post()
        elif path == "/api/workers":
            self.handle_worker_post()
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

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

    def handle_report_post(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            report = json.loads(body)

            mode = get_odoo_mode()

            # === 幂等检查 ===
            idempotency_key = report.get("idempotencyKey", "")
            if idempotency_key:
                existing = db_get_report_by_idempotency(idempotency_key)
                if existing:
                    logger.info(f"幂等请求: {idempotency_key} - 返回已有结果")
                    self.write_json({
                        "ok": True,
                        "data": _normalize_report(existing),
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

            worker_id = str(report["workerId"])
            if worker_id not in get_valid_worker_ids():
                self.write_json({"ok": False, "error": f"工人 {worker_id} 不存在"},
                                status=HTTPStatus.BAD_REQUEST)
                return

            # 新字段: 工单ID
            workorder_id = str(report.get("workorderId", report.get("orderId", "")))
            production_id = str(report.get("productionId", ""))

            operation = str(report["operation"])
            op_info = OPERATION_MAP.get(operation)
            if not op_info:
                self.write_json({"ok": False, "error": f"无效工序: {operation}"},
                                status=HTTPStatus.BAD_REQUEST)
                return

            qty = report["qty"]
            if not isinstance(qty, (int, float)) or qty <= 0:
                self.write_json({"ok": False, "error": "数量必须是正数"},
                                status=HTTPStatus.BAD_REQUEST)
                return

            # === 物料校验 ===
            materials = report.get("materials", [])
            host_type = op_info.get("hostType")
            if host_type and materials:
                # 验证物料是否属于当前主机类型
                valid_codes = TAPE_BOM_CODES if host_type == "tape" else SPLITTER_BOM_CODES
                for mat in materials:
                    code = mat.get("defaultCode", "")
                    if code and code.upper() not in valid_codes:
                        self.write_json({
                            "ok": False,
                            "error": f"物料 {code} 不属于当前主机类型 ({host_type})"
                        }, status=HTTPStatus.BAD_REQUEST)
                        return
                    actual_qty = mat.get("actualQty", 0)
                    if not isinstance(actual_qty, (int, float)) or actual_qty <= 0:
                        self.write_json({"ok": False, "error": f"物料 {code} 实际使用数量必须为正数"},
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
            report.setdefault("errorMessage", "")

            # 兼容旧格式: orderId 用 report UUID 避免唯一约束冲突
            if not report.get("orderId") or report.get("orderId") == "":
                report["orderId"] = f"direct-{report['id'][:8]}"

            # === Mock 模式 ===
            if mode == "mock":
                if db_add_report(report, materials):
                    saved = db_get_report(report["id"]) or report
                    result = _normalize_report(saved) if isinstance(saved, dict) else saved
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
                        "error": "重复报工：该工人已对此工单、此工序报过工"
                    }, status=HTTPStatus.CONFLICT)
                return

            # === 真实模式 - Odoo 写入 ===
            # 调用 Odoo 物料扣减（不依赖工单）
            odoo_result = None
            if materials and not MOCK_MODE:
                try:
                    odoo_result = odoo_deduct_materials(
                        materials=materials,
                        idempotency_key=idempotency_key,
                    )
                    logger.info(f"物料扣减结果: {odoo_result.get('message','')}")
                except Exception as e:
                    logger.error(f"物料扣减异常: {e}")
                    odoo_result = {"ok": False, "error": str(e)}

            # 保存到 SQLite
            report["odooReportId"] = ""
            report["odooStockMoveIds"] = json.dumps(odoo_result.get("stock_move_ids", [])) if odoo_result else ""

            if odoo_result and odoo_result.get("ok"):
                report["syncStatus"] = "odoo_synced"
                report["odooReportId"] = str(odoo_result.get("workorder_id", workorder_id))
                # 清除所有缓存（线程安全）
                with _BOM_CACHE_LOCK:
                    _BOM_CACHE["data"] = None
                _DASH_CACHE["data"] = None
                _WO_CACHE["data"] = None
            elif odoo_result:
                report["syncStatus"] = "odoo_failed"
                errors_list = odoo_result.get("errors", [odoo_result.get("error", "未知错误")])
                report["errorMessage"] = "; ".join(errors_list) if isinstance(errors_list, list) else errors_list

            if db_add_report(report, materials):
                saved = db_get_report(report["id"]) or report
                result = _normalize_report(saved) if isinstance(saved, dict) else saved

                if odoo_result and odoo_result.get("ok"):
                    if odoo_result.get("partial"):
                        msg = odoo_result.get("message", "部分物料扣减成功")
                    else:
                        msg = "报工成功，已完成物料库存扣减"
                elif materials and not odoo_result:
                    msg = "报工已保存（未提交物料）"
                else:
                    msg = "报工已保存，但物料扣减失败"

                self.write_json({
                    "ok": True,
                    "data": result,
                    "meta": {
                        "mode": "real",
                        "source": "odoo",
                        "message": msg,
                        "odoo_result": odoo_result,
                    },
                })
            else:
                self.write_json({
                    "ok": False,
                    "error": "重复报工：该工人已对此工单、此工序报过工"
                }, status=HTTPStatus.CONFLICT)

        except json.JSONDecodeError:
            self.write_json({"ok": False, "error": "无效的 JSON 格式"}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            logger.error(f"handle_report_post 异常: {exc}", exc_info=True)
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
            existing = get_valid_worker_ids()
            if wid in existing:
                self.write_json({"ok": False, "error": f"工号 {wid} 已存在"}, status=HTTPStatus.CONFLICT)
                return
            db_add_worker(wid, name, team, source, odoo_eid)
            self.write_json({"ok": True, "data": {"id": wid, "name": name, "team": team,
                                                   "source": source, "odooEmployeeId": odoo_eid}})
        except json.JSONDecodeError:
            self.write_json({"ok": False, "error": "无效的 JSON 格式"}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            logger.error(f"handle_worker_post 异常: {exc}", exc_info=True)
            self.write_json({"ok": False, "error": f"服务器错误: {exc}"},
                            status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def dashboard_payload(self):
        try:
            return {"ok": True, "data": load_dashboard()}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def report_stats_payload(self):
        try:
            today = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
            reports = db_reports(date_filter=today)
            total_qty = sum(int(r.get("qty", 0)) for r in reports)
            total_hours = sum(float(r.get("hours", 0)) for r in reports)
            unique_workers = len({r.get("worker_name") for r in reports})
            mode = get_odoo_mode()
            return {
                "ok": True,
                "data": {
                    "todayCount": len(reports),
                    "todayOutput": total_qty,
                    "todayHours": round(total_hours, 1),
                    "activeWorkers": unique_workers,
                    "recentReports": [{
                        "workerName": r["worker_name"], "workerTeam": r.get("worker_team", ""),
                        "orderId": r["order_id"], "qty": r["qty"],
                        "operationLabel": r["operation_label"], "operation": r["operation"],
                        "hours": r["hours"], "time": r["time"],
                    } for r in reports[-8:]],
                },
                "meta": {"mode": mode, "source": "odoo" if mode == "real" else "mock"},
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def write_json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
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
    logger.info(f"Odoo: {ODOO_URL} db={ODOO_DB} user={ODOO_USER}")
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
