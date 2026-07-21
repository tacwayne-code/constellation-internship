import json
import os
import re
import xmlrpc.client
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


BASE_DIR = Path(__file__).resolve().parent
ODOO_URL = os.getenv("ODOO_URL", "http://x.inspiri.cn").rstrip("/")
ODOO_DB = os.getenv("ODOO_DB", "inspiri_erp")
ODOO_USER = os.getenv("ODOO_USER", "ai_test")
ODOO_PASSWORD = os.getenv("ODOO_PASSWORD", "")
LOCAL_TZ = timezone(timedelta(hours=8))


class OdooError(RuntimeError):
    pass


class OdooClient:
    def __init__(self):
        self.uid = None
        self.common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common", allow_none=True)
        self.models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)

    def authenticate(self):
        if not ODOO_PASSWORD:
            raise OdooError("缺少 ODOO_PASSWORD")
        uid = self.common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASSWORD, {})
        if not uid:
            raise OdooError("Odoo 登录失败")
        self.uid = uid
        return uid

    def call(self, model, method, args=None, kwargs=None):
        if self.uid is None:
            self.authenticate()
        return self.models.execute_kw(
            ODOO_DB,
            self.uid,
            ODOO_PASSWORD,
            model,
            method,
            args or [],
            kwargs or {},
        )

    def search_read(self, model, domain, fields, limit=100, order=None):
        kwargs = {"fields": fields, "limit": limit}
        if order:
            kwargs["order"] = order
        return self.call(model, "search_read", [domain], kwargs)

    def read(self, model, ids, fields):
        if not ids:
            return []
        return self.call(model, "read", [ids], {"fields": fields})


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
        "draft": "草稿",
        "confirmed": "待生产",
        "progress": "生产中",
        "to_close": "待关闭",
        "done": "完成",
        "cancel": "取消",
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
            "生产规划",
            mrp_labels.get(mrp_state, mrp_state or "已录入"),
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

    if need_qty > 0:
        return [
            ["销售订单", "完成", "done"],
            stock_stage,
            purchase_stage,
            production_stage,
            delivery_stage,
        ]
    return [
        ["销售订单", "完成", "done"],
        stock_stage,
        purchase_stage,
        production_stage,
        delivery_stage,
    ]


def load_dashboard():
    client = OdooClient()
    client.authenticate()
    recent_start = (datetime.now(LOCAL_TZ) - timedelta(days=7)).astimezone(timezone.utc)
    recent_start_text = recent_start.strftime("%Y-%m-%d %H:%M:%S")

    op_fields = [
        "name",
        "product_id",
        "spec_info",
        "qty_on_hand",
        "qty_forecast",
        "qty_to_order",
        "product_uom_name",
        "product_supplier_id",
        "write_date",
    ]
    orderpoint_rows = client.search_read(
        "stock.warehouse.orderpoint",
        [["qty_to_order", ">", 0]],
        op_fields,
        limit=80,
        order="write_date desc",
    )
    ops_by_product_id = {rel_id(row.get("product_id")): row for row in orderpoint_rows}
    ops_by_code = {product_code(row.get("product_id")): row for row in orderpoint_rows}

    order_fields = [
        "name",
        "partner_id",
        "user_id",
        "state",
        "date_order",
        "expected_date",
        "commitment_date",
        "delivery_status",
        "amount_total",
        "write_date",
    ]
    recent_orders = client.search_read(
        "sale.order",
        [["state", "=", "sale"], ["write_date", ">=", recent_start_text]],
        order_fields,
        limit=160,
        order="write_date desc",
    )
    recent_order_ids = [row["id"] for row in recent_orders]

    line_fields = [
        "order_id",
        "product_id",
        "default_code",
        "spec_info",
        "name",
        "product_uom_qty",
        "qty_delivered",
        "qty_to_deliver",
        "product_uom",
        "state",
        "scheduled_date",
        "create_date",
        "write_date",
    ]
    recent_line_rows = client.search_read(
        "sale.order.line",
        [["state", "=", "sale"], ["write_date", ">=", recent_start_text]],
        line_fields,
        limit=200,
        order="write_date desc",
    )
    linked_line_rows = []
    if recent_order_ids:
        linked_line_rows = client.search_read(
            "sale.order.line",
            [["state", "=", "sale"], ["order_id", "in", recent_order_ids]],
            line_fields,
            limit=200,
            order="write_date desc",
        )
    sale_line_map = {row["id"]: row for row in recent_line_rows}
    sale_line_map.update({row["id"]: row for row in linked_line_rows})
    sale_lines = list(sale_line_map.values())
    order_ids = sorted({rel_id(line.get("order_id")) for line in sale_lines if rel_id(line.get("order_id"))})
    orders = {row["id"]: row for row in recent_orders}
    missing_order_ids = [order_id for order_id in order_ids if order_id not in orders]
    orders.update({row["id"]: row for row in client.read("sale.order", missing_order_ids, order_fields)})

    mrp_rows = []
    try:
        mrp_rows = client.search_read(
            "mrp.production",
            [["state", "not in", ["done", "cancel"]]],
            ["name", "origin", "product_id", "product_qty", "qty_produced", "state", "reservation_state", "date_start", "date_deadline", "write_date"],
            limit=120,
            order="write_date desc",
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
            line.get("write_date"),
            order.get("write_date"),
            op.get("write_date"),
            (mrp or {}).get("write_date"),
        )

        total_qty += qty
        delivered_qty += delivered
        remaining_qty += remaining
        delivery_rows.append(
            {
                "customer": rel_name(order.get("partner_id"), "-"),
                "customerCode": bracket_code(rel_name(order.get("partner_id"), "-")),
                "order": rel_name(line.get("order_id"), "-"),
                "machine": product,
                "code": code,
                "spec": spec,
                "qty": qty_text(qty),
                "uom": rel_name(line.get("product_uom"), ""),
                "remark": remark,
                "splitter": "-",
                "delivery": status,
                "owner": rel_name(order.get("user_id"), "-"),
                "date": local_time(display_due),
                "updated": local_dt(updated_at),
                "remaining": qty_text(remaining),
                "priority": "danger" if status == "已逾期" else "warning" if status in ("待采购", "待下单", "已规划") else "running",
                "stages": build_stages(qty, delivered, remaining, need_qty, op.get("product_supplier_id"), mrp, status),
                "_sort": order_number(rel_name(line.get("order_id"), "")),
                "_updated_ts": updated_at.timestamp() if updated_at else 0,
            }
        )

    delivery_rows.sort(key=lambda row: (row.get("_updated_ts", 0), row.get("_sort", 0)), reverse=True)

    replenish_rows = []
    recent_orderpoint_rows = [row for row in orderpoint_rows if (parse_dt(row.get("write_date")) or datetime.min.replace(tzinfo=timezone.utc)) >= recent_start]
    for row in recent_orderpoint_rows:
        product = clean_name(row.get("product_id"))
        code = product_code(row.get("product_id"))
        replenish_rows.append(
            {
                "product": product,
                "code": code,
                "spec": row.get("spec_info") or "-",
                "onHand": qty_text(row.get("qty_on_hand")),
                "forecast": qty_text(row.get("qty_forecast")),
                "toOrder": qty_text(row.get("qty_to_order")),
                "uom": row.get("product_uom_name") or "",
                "supplier": rel_name(row.get("product_supplier_id"), "待配供应商"),
                "updated": local_time(row.get("write_date")),
            }
        )

    pending_rows = delivery_rows
    pending_order_count = len({row["order"] for row in pending_rows})
    replenish_qty = sum(number(row.get("qty_to_order")) for row in recent_orderpoint_rows)
    supplier_missing = sum(1 for row in recent_orderpoint_rows if number(row.get("qty_to_order")) > 0 and not row.get("product_supplier_id"))
    active_mrp_count = 0
    try:
        active_mrp_count = client.call(
            "mrp.production",
            "search_count",
            [[["state", "not in", ["done", "cancel"]]]],
            {},
        )
    except Exception:
        active_mrp_count = 0

    kpis = [
        ["最近待处理", str(pending_order_count), "单", "最近7天更新", "#3b82f6"],
        ["待处理行", str(len(pending_rows)), "行", "交付后自动消失", "#22b8cf"],
        ["待交付数量", qty_text(remaining_qty), "台/套", "qty_to_deliver 汇总", "#20b26b"],
        ["补货缺口", qty_text(replenish_qty), "台", f"近7天补货 {len(recent_orderpoint_rows)} 条", "#f07a35"],
        ["待配供应商", str(supplier_missing), "条", "补货规则未配置供应商", "#eab842"],
        ["生产规划", "待录入", "", "Odoo暂无真实工序进度", "#8b73e6"],
        ["数据来源", "Odoo", "", "最近更新订单", "#eab842"],
    ]

    alerts = []
    for row in delivery_rows[:3]:
        alerts.append(
            [
                row["delivery"],
                row["order"],
                f"{row['customer']} · {row['machine']} {row['spec']} · 数量 {row['qty']}，{row['remark']}，更新 {row['updated']}",
                row["owner"],
            ]
        )
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

    return {
        "kpis": kpis,
        "deliveryRows": delivery_rows[:12],
        "replenishments": replenish_rows[:6],
        "latestOrders": latest_orders,
        "alerts": alerts,
        "meta": {
            "source": "odoo",
            "db": ODOO_DB,
            "user": ODOO_USER,
            "updatedAt": datetime.now(LOCAL_TZ).isoformat(timespec="seconds"),
            "accuracyNote": "客户、订单、产品、规格、数量、待交付、补货缺口、供应商配置均来自 Odoo 原字段；ERP流程进度由这些字段推导，生产工序进度当前未在 Odoo 维护。",
            "range": "最近7天更新",
            "progressNote": "核心区只展示最近7天更新且仍待交付的订单行；问题解决后会自动从主列表消失。",
        },
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/dashboard":
            self.write_json(self.dashboard_payload())
            return
        if path == "/api/health":
            self.write_json({"ok": True})
            return
        super().do_GET()

    def dashboard_payload(self):
        try:
            return {"ok": True, "data": load_dashboard()}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def write_json(self, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    port = int(os.getenv("PORT", "8090"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"production dashboard: http://0.0.0.0:{port}")
    print(f"Odoo: {ODOO_URL} db={ODOO_DB} user={ODOO_USER}")
    server.serve_forever()


if __name__ == "__main__":
    main()
