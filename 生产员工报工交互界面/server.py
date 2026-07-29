import json
import logging
import os
import re
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
                _val = _val.strip().strip('"').strip("'")
                os.environ.setdefault(_key, _val)

PORT = int(os.getenv("PORT", "8089"))
ODOO_URL = os.getenv("ODOO_URL", "http://127.0.0.1:8069")
ODOO_DB = os.getenv("ODOO_DB", "odoo")
ODOO_USER = os.getenv("ODOO_USER", "admin")
ODOO_PASSWORD = os.getenv("ODOO_PASSWORD", "")
MOCK_MODE = os.getenv("ODOO_MOCK_MODE", "false").lower() == "true"
DB_FILE = Path(__file__).resolve().parent / "data.db"
WHITE_EXT = {".html", ".css", ".js", ".svg", ".ico", ".png"}
BASE_DIR = Path(__file__).resolve().parent
LOCAL_TZ = timezone(timedelta(hours=8))

# ---- 日志 ----
logging.basicConfig(
    level=logging.INFO,
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

# ---- 并发锁 ----
DB_LOCK = threading.Lock()
ODOO_LOCK = threading.Lock()


# ============================================================
# OdooClient
# ============================================================

class OdooError(Exception):
    pass


class OdooClient:
    def __init__(self):
        self._uid = None
        import socket
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


# ---- 客户端单例 ----
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
# SQLite 数据层（最小化——看板独占）
# ============================================================

def _migrate_db():
    with DB_LOCK:
        conn = sqlite3.connect(str(DB_FILE))
        conn.execute("PRAGMA journal_mode=WAL")
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS dashboard_cache (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        conn.commit()
        conn.close()
    logger.info("SQLite 迁移完成 (DB: %s)", DB_FILE)


def _init_db():
    _migrate_db()

_migrate_db()

# ============================================================
# 工具函数
# ============================================================


def rel_id(display):
    if isinstance(display, (list, tuple)) and len(display) >= 1:
        return display[0]
    return display


def rel_name(display, default=""):
    if isinstance(display, (list, tuple)) and len(display) >= 2:
        return display[1]
    return str(display or default)


def product_code(display, default=""):
    text = rel_name(display)
    m = re.match(r"^\[([^\]]+)\]", text)
    return m.group(1) if m else default


def clean_name(display):
    return product_code(display) or rel_name(display, "-")


def number(value):
    return float(value or 0)


def due_state(due_str, remaining):
    if not due_str or remaining <= 0:
        return None
    try:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(due_str[:19], fmt)
                break
            except ValueError:
                continue
        else:
            return None
        now = datetime.now()
        if dt < now:
            return "danger"
        if (dt - now).days <= 3:
            return "warning"
        return "running"
    except Exception:
        return None


def max_dt(*values):
    best = None
    for v in values:
        if v and (best is None or v > best):
            best = v
    return best or ""


# ============================================================
# 仪表盘数据加载（从 Odoo 同步）
# ============================================================

_DASH_CACHE = {"data": None, "ts": 0}
_DASH_CACHE_TTL = 120


def load_dashboard():
    now = time.time()
    if _DASH_CACHE["data"] is not None and (now - _DASH_CACHE["ts"]) < _DASH_CACHE_TTL:
        logger.info("Dashboard缓存命中")
        return _DASH_CACHE["data"]

    client = get_odoo()
    recent_start = (datetime.now(LOCAL_TZ) - timedelta(days=7)).astimezone(timezone.utc)
    recent_start_text = recent_start.strftime("%Y-%m-%d %H:%M:%S")

    # ---- 补货单 ----
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

    # ---- 销售订单 + 订单行 ----
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

    # ---- 制造订单 ----
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

    # ---- 组装交付行 ----
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
        remark = "补货缺口 " + str(int(need_qty)) if need_qty > 0 else ("已交付" if remaining <= 0 else "Odoo待交付")
        updated_at = max_dt(
            line.get("write_date"), order.get("write_date"),
            op.get("write_date"), (mrp or {}).get("write_date"),
        )
        total_qty += qty
        delivered_qty += delivered
        remaining_qty += remaining
        delivery_rows.append({
            "customer": rel_name(order.get("partner_id"), "-"),
            "customerCode": product_code(order.get("partner_id"), "-"),
            "order": rel_name(line.get("order_id"), "-"),
            "machine": product, "code": code, "spec": spec,
            "qty": int(qty), "delivered": int(delivered), "remaining": int(remaining),
            "due": display_due or "-", "status": status or "-",
            "remark": remark, "updated": updated_at,
            "partnerCode": product_code(order.get("partner_id"), "-"),
            "orderCustomer": rel_name(order.get("partner_id"), "-"),
            "scheduledDate": (line.get("scheduled_date") or "")[:10],
            "createDate": (line.get("create_date") or "")[:10],
            "orderState": order.get("state", ""),
            "productUom": rel_name(line.get("product_uom"), "PCE"),
            "lineState": line.get("state", ""),
            "qtyDelivered": int(delivered),
            "qtyToDeliver": int(remaining),
            "commitmentDate": (due or "")[:10] if due else "",
            "amountTotal": order.get("amount_total", 0),
        })

    # ---- 补货项 ----
    replenishments = []
    for op_row in orderpoint_rows:
        product = clean_name(op_row.get("product_id"))
        spec = op_row.get("spec_info", "-")
        qty_to_order = number(op_row.get("qty_to_order"))
        if qty_to_order <= 0:
            continue
        replenishments.append({
            "code": product_code(op_row.get("product_id"), product),
            "name": product, "spec": spec,
            "qty": int(number(op_row.get("qty_forecast"))),
            "qtyToOrder": int(qty_to_order),
            "qtyOnHand": int(number(op_row.get("qty_on_hand"))),
            "uom": rel_name(op_row.get("product_uom_name"), "PCE"),
            "supplier": rel_name(op_row.get("product_supplier_id"), "-"),
            "updated": op_row.get("write_date", ""),
        })

    # ---- KPI ----
    active_order_count = len({r["order"] for r in delivery_rows})
    pending_count = len([r for r in delivery_rows if r["status"] in ("warning", "danger")])
    kpis = [
        ["活跃订单", str(active_order_count), "个", "今日新增", "#f59e0b"],
        ["待交付", str(int(remaining_qty)), "台", "总量 " + str(int(total_qty)), "#3b82f6"],
        ["紧迫订单", str(pending_count), "条", "超期或 3 天内", "#e14d4d"],
        ["已交付", str(int(delivered_qty)), "台",
         str(int(delivered_qty / total_qty * 100)) + "%" if total_qty > 0 else "0%", "#20b26b"],
        ["补充需求", str(len(replenishments)), "项", "补货缺口", "#8b73e6"],
        ["在制工单", str(len(mrp_rows)), "条",
         str(len([m for m in mrp_rows if m.get("state") == "progress"])) + " 生产中", "#22b8cf"],
    ]

    result = {
        "kpis": kpis,
        "deliveryRows": delivery_rows,
        "replenishments": replenishments,
        "alerts": [],
        "latestOrders": [],
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
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def write_json(self, data, status=HTTPStatus.OK):
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(payload))
        self.end_headers()
        self.wfile.write(payload)

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
            try:
                data = load_dashboard()
                self.write_json({"ok": True, "data": data,
                                 "meta": {"mode": get_odoo_mode(), "source": "odoo"}})
            except Exception as e:
                logger.error(f"Dashboard load failed: {e}")
                self.write_json({"ok": False, "error": f"data load failed: {e}"},
                                status=HTTPStatus.INTERNAL_SERVER_ERROR)
        elif path == "/api/health":
            self.write_json({"ok": True, "mode": get_odoo_mode()})
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format, *args):
        logger.info("%s - %s", self.client_address[0], format % args)


# ============================================================
# 启动
# ============================================================

def main():
    _init_db()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    logger.info(f"订单交付看板: http://0.0.0.0:{PORT}")
    logger.info(f"Odoo: {ODOO_URL}")
    logger.info(f"模式: {'MOCK' if MOCK_MODE else 'REAL'}")
    logger.info(f"数据库: {DB_FILE}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
