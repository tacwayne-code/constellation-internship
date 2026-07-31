import json
import logging
import os
import re
import signal
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
WHITE_EXT = {".html", ".css", ".js", ".svg", ".ico", ".png"}

# ============================================================
# 日志
# ============================================================

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("order-delivery-dashboard")
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
# SQLite 数据层（看板无需本地存储，仅保留空初始化）
# ============================================================

def _init_db():
    """初始化（看板无需本地表）"""
    logger.info("看板模式：无本地数据表需求，直接使用 Odoo 数据")


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
_DASH_CACHE_TTL = 120
_DASH_LOCK = threading.Lock()


def load_dashboard():
    """加载Dashboard数据（2分钟缓存，双重检查锁防并发穿透）"""
    now = time.time()

    # 无锁快读 —— 命中时零开销
    if _DASH_CACHE["data"] is not None and (now - _DASH_CACHE["ts"]) < _DASH_CACHE_TTL:
        logger.info("Dashboard缓存命中")
        return _DASH_CACHE["data"]

    # 双重检查：加锁后再次验证，防止多线程重复查 Odoo
    with _DASH_LOCK:
        if _DASH_CACHE["data"] is not None and (now - _DASH_CACHE["ts"]) < _DASH_CACHE_TTL:
            logger.info("Dashboard缓存命中（锁内确认）")
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
_WO_CACHE_TTL = 60  # 30秒缓存，准实时


def get_workorders_data():
    """
    获取活跃工单列表（60秒缓存，准实时）
    过滤条件：
      - 所属 MO 未完成（state 非 done/cancel）
      - MO 从昨天开始（write_date >= 昨天 00:00，排除更早的历史数据）
      - 工单有 operation_id + workcenter_id
      - 所属 MO 的 BOM 必须有 routing（工艺过程已配置）
      - 工序 PDF 优先但非必须
    """
    now = time.time()
    if _WO_CACHE["data"] is not None and (now - _WO_CACHE["ts"]) < _WO_CACHE_TTL:
        return _WO_CACHE["data"]
    mode = get_odoo_mode()
    try:
        client = get_odoo()

        # 日期下限：昨天 00:00 UTC（不显示更早的历史数据）
        yesterday_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        yesterday_start_text = yesterday_start.strftime("%Y-%m-%d %H:%M:%S")

        # 第一步：找未完成的 MO（不按日期过滤，方便实时同步新 MO）
        mo_fields = ["id", "name", "bom_id", "product_id", "product_qty", "origin", "state", "write_date"]
        mo_rows = client.search_read(
            "mrp.production",
            [("write_date", ">=", yesterday_start_text),
             ("state", "not in", ["done", "cancel"])],
            mo_fields, limit=200, order="write_date desc"
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
            _WO_CACHE["data"] = []
            _WO_CACHE["ts"] = time.time()
            return []
        wo_rows = client.search_read(
            "mrp.workorder",
            [("production_id", "in", list(valid_mo_ids)),
             ("operation_id", "!=", False),
             ("workcenter_id", "!=", False),
             ("state", "not in", ["done", "cancel"])],
            wo_fields, limit=50, order="id desc"
        )
        logger.info(f"工单候选: {len(wo_rows)}条")

        # 第三步：检查工单对应的 routing.workcenter 是否有 PDF
        # 优先显示有 PDF 的，没有 PDF 的也保留（运维可能还未上传）
        op_ids = {rel_id(wo.get("operation_id")) for wo in wo_rows if rel_id(wo.get("operation_id"))}
        ops_with_pdf = set()
        if op_ids:
            op_data = client.read("mrp.routing.workcenter", list(op_ids),
                                  ["id", "worksheet", "worksheet_type"])
            for op in op_data:
                if op.get("worksheet") or op.get("worksheet_type") == "pdf":
                    ops_with_pdf.add(op["id"])
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
            pcode = product_code(wo.get("product_id"))  # 传入 tuple，不要传 pid(int)

            # 确定主机类型（用产品编码，不依赖固定 ID）
            host_type = None
            if pcode == "P04725":
                host_type = "tape"
            elif pcode == "P04726":
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
        if path == "/":
            self.send_response(301)
            self.send_header("Location", "/index.html")
            self.end_headers()
            return
        if ext in WHITE_EXT:
            return super().do_GET()
        self.send_error(HTTPStatus.NOT_FOUND)

    def _route_get_api(self, path, params):
        if path == "/api/dashboard":
            self.write_json(self.dashboard_payload())
        elif path == "/api/health":
            self.write_json({"ok": True, "mode": get_odoo_mode()})
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format, *args):
        logger.info("%s - %s", self.client_address[0], format % args)

    def dashboard_payload(self):
        try:
            return {"ok": True, "data": load_dashboard()}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

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

    port = int(os.getenv("PORT", "8090"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.timeout = 2
    logger.info(f"订单交付流程看板: http://0.0.0.0:{port}")
    logger.info(f"Odoo: {ODOO_URL}")
    logger.info(f"模式: {'模拟 (MOCK)' if MOCK_MODE else '真实 (REAL)'}")

    try:
        while _running:
            server.handle_request()
    except KeyboardInterrupt:
        pass
    logger.info("服务器已关闭")


if __name__ == "__main__":
    main()
