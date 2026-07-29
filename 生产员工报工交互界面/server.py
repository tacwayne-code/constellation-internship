import json
import logging
import os
import re
import signal
import sqlite3
import threading
import time
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
    logger.info(f"Odoo: {ODOO_URL}")
    logger.debug(f"Odoo 连接详情: db={ODOO_DB} user={ODOO_USER}")

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
_DASH_CACHE_TTL = 120  # 2分钟缓存，准实时


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
# HTTP Handler
# ============================================================

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

    def write_json(self, data, status=HTTPStatus.OK):
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(payload))
        self.end_headers()
        self.wfile.write(payload)

    def dashboard_payload(self):
        try:
            data = load_dashboard()
            return {"ok": True, "data": data,
                    "meta": {"mode": get_odoo_mode(), "source": "odoo"}}
        except Exception as e:
            logger.error(f"仪表板数据加载失败: {e}")
            return {"ok": False, "error": f"数据加载失败: {e}"}

    def _route_get_api(self, path, params):
        if path == "/api/dashboard":
            self.write_json(self.dashboard_payload())
        elif path == "/api/health":
            self.write_json({"ok": True, "mode": get_odoo_mode()})
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
        # 细节版只看板，无报工 API
        self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format, *args):
        logger.info("%s - %s", self.client_address[0], format % args)

    # ---- API 实现 ----

def main():
    _init_db()
    port = int(os.getenv("PORT", "8089"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    logger.info(f"生产员工报工系统: http://0.0.0.0:{port}")
    logger.info(f"Odoo: {ODOO_URL}")
    logger.info(f"模式: {'模拟 (MOCK)' if MOCK_MODE else '真实 (REAL)'}")
    logger.info(f"数据库: {DB_FILE}")
    logger.info(f"数据库: {DB_FILE}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()

if __name__ == "__main__":
    main()
