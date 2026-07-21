import json
import os
import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.cookiejar import CookieJar
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener
from uuid import uuid4


BASE_DIR = Path(__file__).resolve().parent
TRACKING_FILE = BASE_DIR / "replenishment_tracking.json"
MANUAL_URGENT_FILE = BASE_DIR / "manual_urgent_purchase.json"
OVERRIDES_FILE = BASE_DIR / "overrides.json"
REPLENISHMENT_AUTO_FILE = BASE_DIR / "replenishment_auto_tracking.json"
TRACKING_LOCK = Lock()
MANUAL_URGENT_LOCK = Lock()
OVERRIDES_LOCK = Lock()
REPLENISHMENT_AUTO_LOCK = Lock()
ODOO_URL = os.getenv("ODOO_URL", "http://x.inspiri.cn").rstrip("/")
ODOO_DB = os.getenv("ODOO_DB", "inspiri_erp")
ODOO_USER = os.getenv("ODOO_USER", "")
ODOO_PASSWORD = os.getenv("ODOO_PASSWORD", "")
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "180"))
PRODUCT_SEARCH_TTL_SECONDS = int(os.getenv("PRODUCT_SEARCH_TTL_SECONDS", "600"))
DASHBOARD_MAX_ORDERS = int(os.getenv("DASHBOARD_MAX_ORDERS", "1200"))
DASHBOARD_MAX_MOVES = int(os.getenv("DASHBOARD_MAX_MOVES", "8000"))

READ_ONLY_METHODS = {"search_read", "search_count", "read", "fields_get", "name_search", "search"}


STATE_LABELS = {
    "draft": "询价",
    "sent": "已发送",
    "to approve": "待审批",
    "purchase": "采购订单",
    "done": "已完成",
    "cancel": "已取消",
    "assigned": "可用",
    "waiting": "等待中",
    "confirmed": "等待中",
}


class OdooError(RuntimeError):
    pass


class OdooClient:
    def __init__(self):
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))
        self.uid = None

    def json_rpc(self, path, params):
        payload = json.dumps(
            {"jsonrpc": "2.0", "method": "call", "params": params},
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            f"{ODOO_URL}{path}",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        for attempt in range(3):
            try:
                with self.opener.open(request, timeout=25) as response:
                    data = json.loads(response.read().decode("utf-8"))
                break
            except HTTPError as exc:
                if exc.code not in {502, 503, 504} or attempt == 2:
                    raise
                time.sleep(1.5 * (attempt + 1))
            except (URLError, TimeoutError):
                if attempt == 2:
                    raise
                time.sleep(1.5 * (attempt + 1))
        if data.get("error"):
            detail = data["error"].get("data", {}).get("message") or data["error"].get("message")
            raise OdooError(detail or "Odoo JSON-RPC error")
        return data.get("result")
    def authenticate(self):
        if not ODOO_USER:
            raise OdooError("缺少 ODOO_USER 环境变量")
        if not ODOO_PASSWORD:
            raise OdooError("缺少 ODOO_PASSWORD 环境变量")
        result = self.json_rpc(
            "/web/session/authenticate",
            {"db": ODOO_DB, "login": ODOO_USER, "password": ODOO_PASSWORD},
        )
        uid = result.get("uid") if isinstance(result, dict) else None
        if not uid:
            raise OdooError("Odoo 登录失败")
        self.uid = uid
        return result

    def call_kw(self, model, method, args=None, kwargs=None):
        if method not in READ_ONLY_METHODS:
            raise OdooError(f"禁止调用非只读 Odoo 方法: {model}.{method}")
        if self.uid is None:
            self.authenticate()
        return self.json_rpc(
            f"/web/dataset/call_kw/{model}/{method}",
            {
                "model": model,
                "method": method,
                "args": args or [],
                "kwargs": kwargs or {},
            },
        )

    def search_read(self, model, fields, domain=None, limit=100, order=None):
        kwargs = {"fields": fields, "limit": limit}
        if order:
            kwargs["order"] = order
        return self.call_kw(model, "search_read", [domain or []], kwargs)

    def search_read_all(self, model, fields, domain=None, order=None, page_size=500, max_rows=None):
        rows = []
        offset = 0
        while True:
            if max_rows is not None and len(rows) >= max_rows:
                break
            kwargs = {"fields": fields, "limit": page_size, "offset": offset}
            if order:
                kwargs["order"] = order
            page = self.call_kw(model, "search_read", [domain or []], kwargs)
            if not page:
                break
            rows.extend(page)
            if max_rows is not None and len(rows) >= max_rows:
                rows = rows[:max_rows]
                break
            if len(page) < page_size:
                break
            offset += page_size
        return rows

    def search_count(self, model, domain=None):
        return self.call_kw(model, "search_count", [domain or []], {})


def first_text(value, fallback="-"):
    if isinstance(value, list) and len(value) > 1:
        return str(value[1])
    if value in (False, None, ""):
        return fallback
    return str(value)


def parse_dt(value):
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def money(value):
    return f"¥{float(value or 0):,.2f}"


def now_text():
    return datetime.now().isoformat(timespec="seconds")


def load_replenishment_auto_tracking():
    with REPLENISHMENT_AUTO_LOCK:
        if not REPLENISHMENT_AUTO_FILE.exists():
            return {"records": {}}
        try:
            data = json.loads(REPLENISHMENT_AUTO_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"records": {}}
        if not isinstance(data, dict) or not isinstance(data.get("records"), dict):
            return {"records": {}}
        return data


def save_replenishment_auto_tracking(data):
    with REPLENISHMENT_AUTO_LOCK:
        tmp = REPLENISHMENT_AUTO_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(REPLENISHMENT_AUTO_FILE)


def mark_replenishment_purchased(record_id):
    data = load_replenishment_auto_tracking()
    record = data["records"].get(str(record_id))
    if not record:
        raise ValueError("补货记录尚未同步，请先刷新看板")
    if record.get("status") == "completed":
        raise ValueError("补货记录已经完成入库")
    record["purchaseConfirmed"] = True
    record["purchasedAt"] = now_text()
    save_replenishment_auto_tracking(data)
    return {"id": str(record_id), "purchaseConfirmed": True}


def apply_replenishment_auto_completion(items):
    """Hide a request after on-hand growth reaches its originally requested quantity."""
    data = load_replenishment_auto_tracking()
    records = data["records"]
    visible = []
    completed_count = 0

    for item in items:
        record_id = str(item.get("id") or "")
        if not record_id:
            visible.append(item)
            continue
        current_stock = float(item.get("qtyOnHand") or 0)
        current_request = float(item.get("qtyToOrder") or 0)
        updated_at = str(item.get("updatedAt") or "")
        record = records.get(record_id)

        if not record:
            record = {
                "product": item.get("product") or "",
                "baselineQtyOnHand": current_stock,
                "requestQty": current_request,
                "lastOdooQtyToOrder": current_request,
                "requestUpdatedAt": updated_at,
                "status": "active",
                "startedAt": now_text(),
                "completedAt": "",
                "completedOdooUpdatedAt": "",
                "purchaseConfirmed": False,
                "purchasedAt": "",
            }
            records[record_id] = record
        elif record.get("status") == "completed":
            completed_update = str(record.get("completedOdooUpdatedAt") or "")
            last_request = float(record.get("lastOdooQtyToOrder") or 0)
            is_new_round = current_request > 0 and (
                last_request <= 0 or (updated_at and completed_update and updated_at != completed_update)
            )
            if is_new_round:
                record.update({
                    "baselineQtyOnHand": current_stock,
                    "requestQty": current_request,
                    "requestUpdatedAt": updated_at,
                    "status": "active",
                    "startedAt": now_text(),
                    "completedAt": "",
                    "completedOdooUpdatedAt": "",
                "purchaseConfirmed": False,
                "purchasedAt": "",
                })
            else:
                record["lastOdooQtyToOrder"] = current_request
                completed_count += 1
                continue
        elif float(record.get("requestQty") or 0) <= 0 and current_request > 0:
            record.update({
                "baselineQtyOnHand": current_stock,
                "requestQty": current_request,
                "requestUpdatedAt": updated_at,
                "startedAt": now_text(),
            })

        target_qty = float(record.get("requestQty") or 0)
        baseline_qty = float(record.get("baselineQtyOnHand") or 0)
        received_qty = max(current_stock - baseline_qty, 0)
        record["lastOdooQtyToOrder"] = current_request
        record["lastQtyOnHand"] = current_stock
        record["receivedQty"] = round(received_qty, 4)

        purchase_confirmed = bool(record.get("purchaseConfirmed"))
        replenishment_cleared = purchase_confirmed and current_request <= 0
        if replenishment_cleared or (target_qty > 0 and received_qty >= target_qty):
            record["status"] = "completed"
            record["completedAt"] = now_text()
            record["completedOdooUpdatedAt"] = updated_at
            completed_count += 1
            continue

        item["autoTracking"] = {
            "baselineQtyOnHand": baseline_qty,
            "requestQty": target_qty,
            "receivedQty": round(received_qty, 4),
            "remainingReceiptQty": round(max(target_qty - received_qty, 0), 4),
            "canAutoComplete": target_qty > 0,
            "purchaseConfirmed": bool(record.get("purchaseConfirmed")),
        }
        visible.append(item)

    save_replenishment_auto_tracking(data)
    return visible, completed_count

def load_tracking():
    with TRACKING_LOCK:
        if not TRACKING_FILE.exists():
            return {"requests": {}}
        try:
            data = json.loads(TRACKING_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"requests": {}}
        if not isinstance(data, dict):
            return {"requests": {}}
        requests = data.get("requests")
        if not isinstance(requests, dict):
            data["requests"] = {}
        return data


def save_tracking(data):
    with TRACKING_LOCK:
        tmp = TRACKING_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(TRACKING_FILE)


def load_manual_urgent_items():
    with MANUAL_URGENT_LOCK:
        if not MANUAL_URGENT_FILE.exists():
            return []
        try:
            data = json.loads(MANUAL_URGENT_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return data if isinstance(data, list) else []


def save_manual_urgent_items(items):
    with MANUAL_URGENT_LOCK:
        tmp = MANUAL_URGENT_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(MANUAL_URGENT_FILE)


def parse_number(value, default=0.0):
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def product_matches_open_po(product, open_products):
    target = str(product or "").strip().lower()
    if not target:
        return False
    return any(target == item.lower() or target in item.lower() or item.lower() in target for item in open_products)


def product_key(value):
    return "".join(str(value or "").lower().split())


def find_matching_product(product, candidates):
    target = product_key(product)
    if not target:
        return ""
    for name in candidates:
        key = product_key(name)
        if target == key or target in key or key in target:
            return name
    return ""


def build_stock_snapshot(internal_quants, orderpoints):
    ops_map = orderpoints.get("byProduct", {})
    snapshot = {}
    for q in internal_quants:
        product = first_text(q.get("product_id"))
        if product == "-":
            continue
        entry = snapshot.setdefault(
            product,
            {
                "product": product,
                "qty": 0.0,
                "uom": first_text(q.get("product_uom_id")),
                "location": first_text(q.get("location_id")),
                "minQty": ops_map.get(product, {}).get("min", 0.0),
            },
        )
        entry["qty"] += float(q.get("quantity") or 0)
    return snapshot


def stock_shortage_resolved(stock_entry, fallback_min=0.0):
    if not stock_entry:
        return False
    qty = float(stock_entry.get("qty") or 0)
    min_qty = float(stock_entry.get("minQty") or fallback_min or 0)
    if min_qty > 0:
        return qty >= min_qty
    return qty > 0


def find_open_purchase_info(product, purchase_by_product):
    match = find_matching_product(product, purchase_by_product.keys())
    if not match:
        return {}
    return purchase_by_product.get(match, {})


def enrich_manual_urgent_items(open_product_names, stock_snapshot, purchase_by_product=None):
    purchase_by_product = purchase_by_product or {}
    open_products = [str(item or "").strip() for item in open_product_names if str(item or "").strip()]
    items = load_manual_urgent_items()
    active = []
    stock_names = list(stock_snapshot.keys())
    for item in items:
        product = str(item.get("product") or "").strip()
        matched_product = find_matching_product(product, stock_names)
        stock_entry = stock_snapshot.get(matched_product)
        fallback_min = parse_number(item.get("minQty"))
        if stock_shortage_resolved(stock_entry, fallback_min):
            continue

        purchase_info = find_open_purchase_info(product, purchase_by_product)
        has_open_po = bool(purchase_info) or product_matches_open_po(product, open_products)
        purchase_state = purchase_info.get("status", "")
        qty = float(stock_entry.get("qty") or 0) if stock_entry else parse_number(item.get("qty"))
        min_qty = float(stock_entry.get("minQty") or 0) if stock_entry else fallback_min
        lack_qty = max(min_qty - qty, 0) if min_qty > 0 else parse_number(item.get("lackQty"))
        if purchase_state == "purchase":
            status_text = "已建采购单，待到货"
        elif purchase_state == "rfq":
            status_text = "已有询价，待确认"
        else:
            status_text = "人工急采，待下单"
        enriched = {
            **item,
            "matchedProduct": matched_product,
            "qty": round(qty, 2),
            "minQty": round(min_qty, 2),
            "lackQty": round(lack_qty, 2),
            "hasOpenPO": has_open_po,
            "openPOState": purchase_state,
            "openPOQty": purchase_info.get("remainingQty", 0),
            "openPONames": purchase_info.get("orders", []),
            "status": "waiting_receipt" if purchase_state == "purchase" else ("rfq" if purchase_state == "rfq" else "manual_pending"),
            "statusText": status_text,
        }
        active.append(enriched)
    if len(active) != len(items):
        save_manual_urgent_items(active)
    return active


def add_manual_urgent_item(payload):
    product = str(payload.get("product") or "").strip()
    if not product:
        raise ValueError("缺少产品名称")
    level = str(payload.get("level") or "紧急").strip()
    qty = parse_number(payload.get("qty"))
    min_qty = parse_number(payload.get("minQty"))
    daily_use = parse_number(payload.get("dailyUse"))
    items = load_manual_urgent_items()
    item = {
        "id": uuid4().hex,
        "source": str(payload.get("source") or "manual").strip(),
        "level": level,
        "product": product,
        "qty": qty,
        "purchaseQty": qty,
        "minQty": min_qty,
        "dailyUse": daily_use,
        "lackQty": parse_number(payload.get("lackQty"), max(min_qty - qty, 0)),
        "expectedDate": str(payload.get("expectedDate") or "").strip(),
        "requestDate": str(payload.get("requestDate") or "").strip(),
        "supplier": str(payload.get("supplier") or "").strip(),
        "dept": str(payload.get("dept") or "").strip(),
        "amount": str(payload.get("amount") or "").strip(),
        "reason": str(payload.get("reason") or "").strip(),
        "createdAt": now_text(),
    }
    items.insert(0, item)
    save_manual_urgent_items(items)
    tracking_request({"product": product, "qty": qty, "dailyUse": daily_use, "source": payload.get("source","warehouse")})
    if str(payload.get("skipPending") or "").strip():
        tracking_confirm({"product": product})
    return item


def remove_manual_urgent_item(payload):
    item_id = str(payload.get("id") or "").strip()
    product = str(payload.get("product") or "").strip()
    items = load_manual_urgent_items()
    active = [
        item for item in items
        if not ((item_id and item.get("id") == item_id) or (product and item.get("product") == product))
    ]
    save_manual_urgent_items(active)
    return active


def load_overrides():
    with OVERRIDES_LOCK:
        if not OVERRIDES_FILE.exists():
            return {}
        try:
            return json.loads(OVERRIDES_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}


def save_overrides(data):
    with OVERRIDES_LOCK:
        tmp = OVERRIDES_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(OVERRIDES_FILE)


def is_removed_override(override):
    if not isinstance(override, dict):
        return False
    if override.get("removed") is True:
        return True
    return (
        float(override.get("qty") or 0) >= 99999
        and float(override.get("minQty") or 0) <= 0
        and float(override.get("qtyToOrder") or 0) <= 0
    )


def tracking_request(payload):
    product = str(payload.get("product") or "").strip()
    if not product:
        raise ValueError("缺少产品名称")
    data = load_tracking()
    key = f"{product}__{uuid4().hex[:6]}"
    data["requests"][key] = {
        "id": key,
        "product": product,
        "qty": payload.get("qty"),
        "minQty": payload.get("minQty"),
        "qtyToOrder": payload.get("qtyToOrder"),
        "dailyUse": payload.get("dailyUse"),
        "source": str(payload.get("source") or "warehouse").strip(),
        "status": "pending",
        "requestedAt": now_text(),
        "confirmedAt": "",
    }
    save_tracking(data)
    return data["requests"][key]


def tracking_confirm(payload):
    tid = str(payload.get("id") or "").strip()
    product = str(payload.get("product") or "").strip()
    data = load_tracking()
    if tid and tid in data["requests"]:
        data["requests"][tid]["status"] = "confirmed"
        data["requests"][tid]["confirmedAt"] = now_text()
        save_tracking(data)
        return data["requests"][tid]
    for key, req in data["requests"].items():
        if req.get("product") == product and req.get("status") == "pending":
            data["requests"][key]["status"] = "confirmed"
            data["requests"][key]["confirmedAt"] = now_text()
            save_tracking(data)
            return data["requests"][key]
    raise ValueError(f"未找到待确认的请求: {product}")


def tracking_cancel(payload):
    tid = str(payload.get("id") or "").strip()
    product = str(payload.get("product") or "").strip()
    data = load_tracking()
    if tid and tid in data["requests"]:
        del data["requests"][tid]
        save_tracking(data)
        return {"deleted": tid}
    for key in list(data["requests"].keys()):
        if data["requests"][key].get("product") == product and data["requests"][key].get("status") == "pending":
            del data["requests"][key]
            save_tracking(data)
            return {"deleted": key}
    raise ValueError(f"未找到可撤回的请求: {product}")


def auto_cleanup(stock_snapshot, orderpoints):
    """Remove overrides and tracking entries where Odoo stock has recovered."""
    overrides = load_overrides()
    tracking = load_tracking()
    ops_map = orderpoints.get("byProduct", {})
    cleaned_o = 0
    cleaned_t = 0
    for product in list(overrides.keys()):
        matched = find_matching_product(product, list(stock_snapshot.keys()))
        if not matched:
            continue
        entry = stock_snapshot.get(matched, {})
        qty = float(entry.get("qty") or 0)
        op = ops_map.get(matched, {})
        min_q = op.get("min", 0)
        if min_q > 0 and qty >= min_q:
            del overrides[product]
            cleaned_o += 1
        elif qty > 0 and min_q <= 0:
            del overrides[product]
            cleaned_o += 1
    for key in list(tracking["requests"].keys()):
        req = tracking["requests"].get(key, {})
        product = req.get("product", "")
        matched = find_matching_product(product, list(stock_snapshot.keys()))
        if not matched:
            continue
        entry = stock_snapshot.get(matched, {})
        qty = float(entry.get("qty") or 0)
        op = ops_map.get(matched, {})
        min_q = op.get("min", req.get("minQty") or 0)
        if min_q > 0 and qty >= min_q:
            del tracking["requests"][key]
            cleaned_t += 1
        elif qty > 0 and min_q <= 0:
            del tracking["requests"][key]
            cleaned_t += 1
    if cleaned_o:
        save_overrides(overrides)
    if cleaned_t:
        save_tracking(tracking)
    return cleaned_o, cleaned_t


def status_label(state):
    return STATE_LABELS.get(state, state or "-")


def amount_text(value, suffix=""):
    number = float(value or 0)
    if number >= 10000:
        text = f"{number / 10000:.1f}万"
    elif number >= 1000:
        text = f"{number / 1000:.1f}千"
    else:
        text = f"{number:.0f}"
    return f"{text}{suffix}"


def deadline_label(value, now):
    dt = parse_dt(value)
    if not dt:
        return "-"
    days = (now.date() - dt.date()).days
    if days > 0:
        return f"超期 {days} 天"
    if days == 0:
        return "今天"
    return f"{abs(days)} 天后"


def priority_label(value, now):
    dt = parse_dt(value)
    if not dt:
        return "待处理"
    days = (now.date() - dt.date()).days
    if days > 7:
        return "紧急"
    if days > 0:
        return "超期"
    if days == 0:
        return "今日"
    return "待办"


def month_key(dt):
    return dt.strftime("%Y-%m")


def month_label(key):
    return f"{int(key.split('-')[1])}月"


def build_warehouse(client):
    quant_fields = [
        "product_id",
        "location_id",
        "quantity",
        "available_quantity",
        "reserved_quantity",
        "product_uom_id",
    ]
    picking_field_candidates = [
        "name",
        "picking_type_id",
        "state",
        "scheduled_date",
        "date_done",
        "origin",
        "partner_id",
        "user_id",
    ]
    picking_model_fields = client.call_kw(
        "stock.picking",
        "fields_get",
        [],
        {"attributes": ["string"]},
    )
    picking_fields = [field for field in picking_field_candidates if field in picking_model_fields]
    picking_type_fields = [
        "name",
        "count_picking_ready",
        "count_picking_late",
        "count_picking_waiting",
        "count_picking",
        "count_picking_backorders",
        "count_picking_draft",
    ]
    move_fields = ["date", "product_uom_qty", "state", "location_id", "location_dest_id"]
    today = datetime.now(timezone.utc).date()
    trend_start = today - timedelta(days=13)
    quants = client.search_read_all("stock.quant", quant_fields, [], order="id desc")
    pickings = client.search_read_all(
        "stock.picking",
        picking_fields,
        [["state", "not in", ["done", "cancel"]]],
        order="scheduled_date desc",
    )
    picking_types = client.search_read_all("stock.picking.type", picking_type_fields, [], order="id asc")
    moves = client.search_read_all(
        "stock.move",
        move_fields,
        [["state", "=", "done"], ["date", ">=", trend_start.strftime("%Y-%m-%d 00:00:00")]],
        order="date desc",
        max_rows=DASHBOARD_MAX_MOVES,
    )

    internal_quants = [q for q in quants if first_text(q.get("location_id")).startswith("WH/库存")]
    total_products = len({first_text(q.get("product_id")) for q in internal_quants})
    total_rows = len(internal_quants)
    stock_sum = sum(float(q.get("quantity") or 0) for q in internal_quants)

    quants_by_product = defaultdict(list)
    for quant in internal_quants:
        quants_by_product[first_text(quant.get("product_id"))].append(quant)

    display_quants = []
    zero_quants = []
    for product_quants in quants_by_product.values():
        positive_quants = [q for q in product_quants if float(q.get("quantity") or 0) > 0]
        if positive_quants:
            display_quants.extend(positive_quants)
            continue
        representative = dict(product_quants[0])
        representative["quantity"] = sum(float(q.get("quantity") or 0) for q in product_quants)
        display_quants.append(representative)
        zero_quants.append(representative)

    pending_pickings = pickings
    now = datetime.now(timezone.utc)
    late_pickings = [
        p for p in pending_pickings
        if (parse_dt(p.get("scheduled_date")) and parse_dt(p.get("scheduled_date")) < now)
    ]

    type_counter = Counter(first_text(p.get("picking_type_id")) for p in pending_pickings)
    type_stats = {first_text(row.get("name")): row for row in picking_types}
    ops = []
    for row in picking_types:
        name = first_text(row.get("name"))
        ready = int(row.get("count_picking_ready") or 0)
        if ready <= 0:
            continue
        late = int(row.get("count_picking_late") or 0)
        waiting = int(row.get("count_picking_waiting") or 0)
        backorders = int(row.get("count_picking_backorders") or 0)
        if "采购收货" in name:
            meta = f"待接收 {ready} · 迟到 {late} · 缺货订单 {backorders}"
        elif "销售出库" in name:
            meta = f"待送货 {ready} · 等待中 {waiting} · 迟到 {late}"
        else:
            meta = f"可操作 {ready} · 迟到 {late}"
        ops.append([name, meta, ready])

    def stat_value(keyword, field):
        return sum(
            int(row.get(field) or 0)
            for name, row in type_stats.items()
            if keyword in name
        )

    ready_total = sum(int(row.get("count_picking_ready") or 0) for row in picking_types)
    late_total = sum(int(row.get("count_picking_late") or 0) for row in picking_types)

    location_counts = Counter(first_text(q.get("location_id")) for q in internal_quants)
    max_count = max(location_counts.values(), default=1)
    locations = [
        [name.replace("WH/库存/", ""), max(6, round(count / max_count * 100))]
        for name, count in location_counts.most_common(6)
    ]

    alerts = []
    for q in zero_quants[:10]:
        product = first_text(q.get("product_id"))
        spec = first_text(q.get("product_uom_id"))
        location = first_text(q.get("location_id"))
        alerts.append([product, f"{spec} · {location}", "缺货"])

    actionable_names = [item[0] for item in ops]
    action_pickings = [
        p for p in pending_pickings
        if p.get("state") == "assigned"
        and any(name in first_text(p.get("picking_type_id")) for name in actionable_names)
    ]
    action_pickings.sort(key=lambda p: parse_dt(p.get("scheduled_date")) or now)
    warehouse_action_rows = []
    for picking in action_pickings[:20]:
        doc = first_text(picking.get("name"))
        picking_type = first_text(picking.get("picking_type_id"))
        owner = first_text(picking.get("user_id"), "")
        partner = first_text(picking.get("partner_id"), "")
        origin = first_text(picking.get("origin"), "")
        target = partner or origin or owner or "-"
        warehouse_action_rows.append(
            [
                priority_label(picking.get("scheduled_date"), now),
                doc,
                picking_type,
                target,
                deadline_label(picking.get("scheduled_date"), now),
                status_label(picking.get("state")),
            ]
        )

    rows = []
    sorted_quants = sorted(display_quants, key=lambda q: float(q.get("quantity") or 0), reverse=True)
    for q in sorted_quants:
        qty = float(q.get("quantity") or 0)
        rows.append(
            [
                first_text(q.get("product_id")),
                "-",
                f"{qty:.2f}",
                first_text(q.get("product_uom_id")),
                first_text(q.get("location_id")),
                "正常" if qty > 0 else "缺货",
            ]
        )

    trend_days = [today - timedelta(days=day) for day in range(13, -1, -1)]
    stock_in_by_day = defaultdict(float)
    stock_out_by_day = defaultdict(float)
    for move in moves:
        dt = parse_dt(move.get("date"))
        if not dt:
            continue
        day_key = dt.date()
        qty = float(move.get("product_uom_qty") or 0)
        source = first_text(move.get("location_id"))
        dest = first_text(move.get("location_dest_id"))
        if dest.startswith("WH/库存"):
            stock_in_by_day[day_key] += qty
        if source.startswith("WH/库存"):
            stock_out_by_day[day_key] += qty

    warehouse_trend = {
        "labels": [day.strftime("%m/%d") for day in trend_days],
        "inbound": [round(stock_in_by_day[day], 2) for day in trend_days],
        "outbound": [round(stock_out_by_day[day], 2) for day in trend_days],
    }

    return {
        "warehouseKpis": {
            "purchasePending": next((item[2] for item in ops if item[0] == "采购收货"), 0),
            "purchaseLate": stat_value("采购收货", "count_picking_late"),
            "purchaseBackorders": stat_value("采购收货", "count_picking_backorders"),
            "operations": ready_total,
            "salesPending": next((item[2] for item in ops if item[0] == "销售出库"), 0),
            "salesLate": stat_value("销售出库", "count_picking_late"),
            "salesWaiting": stat_value("销售出库", "count_picking_waiting"),
            "productCount": total_products,
            "late": late_total,
            "zeroStock": len(zero_quants),
            "stockSum": stock_sum,
            "quantTotal": len(quants),
            "pickingPendingTotal": len(pending_pickings),
            "pickingTypeTotal": len(picking_types),
            "moveTrendTotal": len(moves),
        },
        "warehouseOps": ops,
        "locations": locations,
        "alerts": alerts[:4],
        "warehouseRows": rows,
        "warehouseActionRows": warehouse_action_rows,
        "warehouseTrend": warehouse_trend,
        "warehouseIssueRows": rows[-4:] if rows else [],
        "warehouseMetricNotes": {
            "purchasePending": "直接读取 Odoo 库存概览卡片的 count_picking_ready。",
            "operations": "所有作业类型的 count_picking_ready 合计。",
            "productCount": "WH/库存内部库位下的唯一产品数量。",
            "zeroStock": "按产品去重后，没有任何正库存库位的产品数量。",
        },
        "_internalQuants": internal_quants,
        "_zeroQuants": zero_quants,
    }


def build_purchase(client):
    fields = ["name", "partner_id", "user_id", "amount_total", "state", "date_order", "date_planned", "currency_id"]
    total_count = client.search_count("purchase.order", [])
    orders = client.search_read_all("purchase.order", fields, [], order="id desc", max_rows=DASHBOARD_MAX_ORDERS)
    now = datetime.now(timezone.utc)

    non_cancel = [o for o in orders if o.get("state") != "cancel"]
    rfq_orders = [o for o in orders if o.get("state") in ("draft", "sent")]
    sent_count = len(rfq_orders)
    waiting_count = sum(1 for o in orders if o.get("state") == "sent")
    late_count = sum(
        1 for o in rfq_orders
        if parse_dt(o.get("date_planned")) and parse_dt(o.get("date_planned")) < now
    )
    recent_start = now - timedelta(days=7)
    recent_amount = sum(
        float(o.get("amount_total") or 0)
        for o in non_cancel
        if parse_dt(o.get("date_order")) and parse_dt(o.get("date_order")) >= recent_start
    )

    supplier_totals = defaultdict(lambda: {"amount": 0.0, "count": 0})
    for o in non_cancel:
        supplier = first_text(o.get("partner_id"))
        supplier_totals[supplier]["amount"] += float(o.get("amount_total") or 0)
        supplier_totals[supplier]["count"] += 1
    suppliers = [
        [name, money(info["amount"]), f"{info['count']} 单"]
        for name, info in sorted(supplier_totals.items(), key=lambda item: item[1]["amount"], reverse=True)[:6]
    ]

    state_counts = Counter(status_label(o.get("state")) for o in orders)
    colors = ["#23e0b2", "#18d8ff", "#ffbf4d", "#ff6274", "#9d82ff"]
    states = [[name, count, colors[index % len(colors)]] for index, (name, count) in enumerate(state_counts.most_common(5))]

    month_totals = defaultdict(float)
    for o in orders:
        dt = parse_dt(o.get("date_order"))
        if dt:
            month_totals[month_key(dt)] += float(o.get("amount_total") or 0)
    month_keys = sorted(month_totals.keys())[-12:]
    purchase_trend = [round(month_totals[key], 2) for key in month_keys] or [0]

    overdue_orders = []
    for o in rfq_orders:
        planned = parse_dt(o.get("date_planned"))
        if not planned or planned >= now:
            continue
        overdue_orders.append(
            [
                priority_label(o.get("date_planned"), now),
                first_text(o.get("name")),
                first_text(o.get("partner_id")),
                first_text(o.get("user_id")),
                deadline_label(o.get("date_planned"), now),
                money(o.get("amount_total")),
                status_label(o.get("state")),
            ]
        )

    rows = []
    for o in orders[:8]:
        planned = parse_dt(o.get("date_planned"))
        if planned and planned < now and o.get("state") not in ("cancel", "done"):
            deadline = f"{(now.date() - planned.date()).days} 天前"
        else:
            deadline = "-"
        rows.append(
            [
                first_text(o.get("name")),
                first_text(o.get("partner_id")),
                first_text(o.get("user_id")),
                deadline,
                money(o.get("amount_total")),
                status_label(o.get("state")),
            ]
        )

    return {
        "purchaseKpis": {
            "total": total_count,
            "sent": sent_count,
            "waiting": waiting_count,
            "late": late_count,
            "recent7Amount": money(recent_amount),
            "loaded": len(orders),
        },
        "purchaseTrend": purchase_trend,
        "purchaseTrendLabels": [month_label(key) for key in month_keys],
        "suppliers": suppliers,
        "states": states,
        "purchaseRows": rows,
        "purchaseIssueRows": overdue_orders[:6],
        "purchaseActionRows": overdue_orders[:20],
        "purchaseMetricNotes": {
            "total": "purchase.order 全部采购/询价记录数量。",
            "sent": "状态为询价或已发送的 RFQ 数量，对齐 Odoo 询价看板。",
            "late": "RFQ 的预计日期早于当前日期且仍未转采购/完成/取消的数量。",
            "recent7Amount": "非取消采购单中，订单日期在最近 7 天内的采购总金额。",
        },
    }


def build_purchase_lines(client):
    """Get purchase order lines that still have quantity to receive."""
    line_fields = [
        "product_id", "order_id", "product_qty", "qty_received",
        "state", "price_unit", "product_uom", "name",
    ]
    open_orders = client.search_read_all(
        "purchase.order",
        ["id", "name", "state", "partner_id", "date_order", "company_id"],
        [["state", "in", ["draft", "sent", "purchase"]]],
        order="id desc",
        max_rows=DASHBOARD_MAX_ORDERS,
    )
    open_order_ids = [o["id"] for o in open_orders]
    if not open_order_ids:
        return {"lines": [], "orderProductIds": [], "orderMap": {}, "orderPartnerMap": {}, "byProduct": {}, "openOrderCount": 0}

    lines = client.search_read_all(
        "purchase.order.line",
        line_fields,
        [["order_id", "in", open_order_ids]],
        order="id desc",
        max_rows=DASHBOARD_MAX_ORDERS * 8,
    )
    order_map = {}
    order_partner_map = {}
    order_date_map = {}
    order_company_map = {}
    for o in open_orders:
        order_map[o["id"]] = first_text(o.get("name"))
        order_partner_map[o["id"]] = first_text(o.get("partner_id"))
        order_date_map[o["id"]] = o.get("date_order") or ""
        order_company_map[o["id"]] = first_text(o.get("company_id"), "")

    active_lines = []
    by_product = {}
    for line in lines:
        product = first_text(line.get("product_id"))
        if product == "-":
            continue
        ordered = float(line.get("product_qty") or 0)
        received = float(line.get("qty_received") or 0)
        remaining = max(ordered - received, 0)
        if remaining <= 0:
            continue
        order_id = line.get("order_id")[0] if isinstance(line.get("order_id"), list) and line.get("order_id") else None
        order_name = order_map.get(order_id, first_text(line.get("order_id")))
        state = line.get("state") or ""
        line["remaining_qty"] = remaining
        line["order_date"] = order_date_map.get(order_id, "")
        line["company_name"] = order_company_map.get(order_id, "")
        active_lines.append(line)

        status = "purchase" if state == "purchase" else "rfq"
        current = by_product.setdefault(
            product,
            {
                "product": product,
                "status": status,
                "remainingQty": 0.0,
                "orders": [],
                "states": [],
                "orderDates": [],
                "companies": [],
            },
        )
        if current["status"] != "purchase" and status == "purchase":
            current["status"] = "purchase"
        current["remainingQty"] += remaining
        if order_name not in current["orders"]:
            current["orders"].append(order_name)
        if state and state not in current["states"]:
            current["states"].append(state)
        order_date = order_date_map.get(order_id, "")
        if order_date and order_date not in current["orderDates"]:
            current["orderDates"].append(order_date)
        company = order_company_map.get(order_id, "")
        if company and company not in current["companies"]:
            current["companies"].append(company)

    products_in_open = list(by_product.keys())

    return {
        "lines": active_lines,
        "orderProductIds": products_in_open,
        "orderMap": order_map,
        "orderPartnerMap": order_partner_map,
        "orderDateMap": order_date_map,
        "orderCompanyMap": order_company_map,
        "byProduct": by_product,
        "openOrderCount": len(open_orders),
    }


def build_procurement_gap(zero_quants, pol_data):
    """Find zero-stock products WITHOUT any open purchase order line."""
    products_in_open = set(pol_data.get("orderProductIds", []))
    seen = set()
    gap_items = []
    for q in zero_quants:
        pid = first_text(q.get("product_id"))
        if pid in seen or pid == "-":
            continue
        seen.add(pid)
        if pid not in products_in_open:
            gap_items.append([
                pid,
                first_text(q.get("product_uom_id")),
                first_text(q.get("location_id")),
                str(float(q.get("quantity") or 0)),
            ])

    return {
        "gapCount": len(gap_items),
        "gapItems": gap_items[:15],
        "inOpenPO": len(products_in_open),
        "zeroStockUnique": len(seen),
    }


def build_product_suppliers(client, product_names):
    """Read configured product vendors; lowest sequence is the preferred vendor."""
    codes = set()
    for product_name in product_names:
        value = str(product_name or "").strip()
        if value.startswith("[") and "]" in value:
            codes.add(value[1:value.index("]")].strip())
    if not codes:
        return {"byProduct": {}, "supplierRowCount": 0}

    products = client.search_read_all(
        "product.product",
        ["display_name", "default_code", "product_tmpl_id"],
        [["default_code", "in", sorted(codes)]],
        page_size=500,
    )
    product_by_template = {
        row["product_tmpl_id"][0]: row
        for row in products
        if isinstance(row.get("product_tmpl_id"), list) and row.get("product_tmpl_id")
    }
    if not product_by_template:
        return {"byProduct": {}, "supplierRowCount": 0}

    supplier_rows = client.search_read_all(
        "product.supplierinfo",
        ["partner_id", "product_tmpl_id", "product_id", "product_name", "product_code", "min_qty", "price", "delay", "sequence"],
        [["product_tmpl_id", "in", list(product_by_template)]],
        order="sequence asc, id asc",
        page_size=500,
    )
    by_product = {}
    for row in supplier_rows:
        template = row.get("product_tmpl_id")
        template_id = template[0] if isinstance(template, list) and template else None
        product = product_by_template.get(template_id)
        partner = row.get("partner_id")
        if not product or not isinstance(partner, list) or len(partner) < 2:
            continue
        display_name = str(product.get("display_name") or "").strip()
        if not display_name or display_name in by_product:
            continue
        by_product[display_name] = {
            "id": partner[0],
            "name": partner[1],
            "supplierCode": row.get("product_code") or "",
            "supplierProductName": row.get("product_name") or "",
            "minQty": float(row.get("min_qty") or 0),
            "price": float(row.get("price") or 0),
            "delay": int(row.get("delay") or 0),
            "sequence": int(row.get("sequence") or 0),
        }
    return {"byProduct": by_product, "supplierRowCount": len(supplier_rows)}

def build_supplier_contacts(client, orders):
    """Extract supplier phone/email from res.partner for purchase orders."""
    partner_ids = set()
    for o in orders:
        raw = o.get("partner_id")
        if isinstance(raw, list) and len(raw) > 0:
            pid = raw[0]
            if isinstance(pid, int) and pid > 0:
                partner_ids.add(pid)

    if not partner_ids:
        return {}

    partners = client.search_read_all(
        "res.partner",
        ["id", "name", "phone", "email", "mobile", "display_name"],
        [["id", "in", list(partner_ids)]],
    )

    contact_map = {}
    for p in partners:
        pid = p["id"]
        contact_map[str(pid)] = {
            "name": first_text(p.get("display_name") or p.get("name")),
            "phone": first_text(p.get("phone") or p.get("mobile"), ""),
            "email": first_text(p.get("email"), ""),
        }

    # Also build a name-based lookup for compatibility
    for p in partners:
        name = first_text(p.get("display_name") or p.get("name"))
        if name and name != "-":
            contact_map[name] = contact_map.get(str(p["id"]), {})

    return contact_map


def build_replenishment_list(client):
    """Read only the current Odoo Inventory > Replenishment list rows."""
    fields = [
        "product_id",
        "spec_info",
        "product_uom",
        "product_uom_name",
        "product_supplier_id",
        "product_min_qty",
        "product_max_qty",
        "qty_on_hand",
        "qty_forecast",
        "qty_to_order",
        "qty_to_order_manual",
        "qty_to_order_computed",
        "qty_multiple",
        "trigger",
        "location_id",
        "warehouse_id",
        "route_id",
        "company_id",
        "snoozed_until",
        "create_date",
        "write_date",
    ]
    # Odoo Inventory > Replenishment's manual list includes rows even when
    # "To Order" is 0. Newly created replenishment rows appear here with
    # trigger=manual and should stay visible until the Odoo list removes them.
    domain = [["trigger", "=", "manual"]]
    try:
        rows = client.search_read_all("stock.warehouse.orderpoint", fields, domain, order="id desc")
    except OdooError:
        rows = []

    product_ids = sorted({
        op["product_id"][0]
        for op in rows
        if isinstance(op.get("product_id"), list) and op.get("product_id")
    })
    product_stock = {}
    if product_ids:
        stock_rows = client.search_read_all(
            "product.product",
            ["qty_available", "virtual_available"],
            [["id", "in", product_ids]],
            order="id asc",
        )
        product_stock = {row["id"]: row for row in stock_rows}

    items = []
    for op in rows:
        pid = first_text(op.get("product_id"))
        if pid == "-":
            continue
        product_id = op.get("product_id")[0] if isinstance(op.get("product_id"), list) and op.get("product_id") else None
        stock = product_stock.get(product_id, {})
        qty_to_order = float(op.get("qty_to_order") or 0)
        items.append({
            "id": op.get("id"),
            "productId": product_id,
            "product": pid,
            "specInfo": op.get("spec_info") or "",
            "uom": op.get("product_uom_name") or first_text(op.get("product_uom"), ""),
            "supplier": first_text(op.get("product_supplier_id"), ""),
            "min": float(op.get("product_min_qty") or 0),
            "max": float(op.get("product_max_qty") or 0),
            "qtyOnHand": float(stock.get("qty_available", op.get("qty_on_hand")) or 0),
            "qtyForecast": float(stock.get("virtual_available", op.get("qty_forecast")) or 0),
            "qtyToOrder": qty_to_order,
            "qtyToOrderManual": float(op.get("qty_to_order_manual") or 0),
            "qtyToOrderComputed": float(op.get("qty_to_order_computed") or 0),
            "qtyMultiple": float(op.get("qty_multiple") or 0),
            "trigger": op.get("trigger") or "",
            "location": first_text(op.get("location_id"), ""),
            "warehouse": first_text(op.get("warehouse_id"), ""),
            "route": first_text(op.get("route_id"), ""),
            "company": first_text(op.get("company_id"), ""),
            "snoozedUntil": op.get("snoozed_until") or "",
            "createdAt": op.get("create_date") or "",
            "updatedAt": op.get("write_date") or "",
        })

    source_total = len(items)
    items, auto_completed_count = apply_replenishment_auto_completion(items)
    return {
        "items": items,
        "total": len(items),
        "sourceTotal": source_total,
        "autoCompletedCount": auto_completed_count,
        "sourceModel": "stock.warehouse.orderpoint",
        "domain": domain,
    }


def build_orderpoints(client):
    """Get reorder rules: min/max stock levels per product."""
    fields = ["product_id", "product_min_qty", "product_max_qty", "qty_to_order",
              "qty_multiple", "location_id", "warehouse_id", "route_id", "company_id",
              "create_date", "write_date"]
    try:
        all_ops = client.search_read_all("stock.warehouse.orderpoint", fields, [], order="id desc")
    except OdooError:
        all_ops = []
    ops_by_product = {}
    for op in all_ops:
        pid = first_text(op.get("product_id"))
        if pid == "-":
            continue
        ops_by_product[pid] = {
            "min": float(op.get("product_min_qty") or 0),
            "max": float(op.get("product_max_qty") or 0),
            "qtyToOrder": float(op.get("qty_to_order") or 0),
            "company": first_text(op.get("company_id"), ""),
            "createdAt": op.get("create_date") or "",
            "updatedAt": op.get("write_date") or "",
        }
    return {"byProduct": ops_by_product, "total": len(all_ops)}


def build_consumption_rates(client):
    """Calculate daily consumption rate from 30 days of stock.move data."""
    today = datetime.now(timezone.utc).date()
    trend_start = today - timedelta(days=30)
    move_fields = ["product_id", "date", "product_uom_qty", "state",
                   "location_id", "location_dest_id"]
    moves = client.search_read_all(
        "stock.move",
        move_fields,
        [["state", "=", "done"], ["date", ">=", trend_start.strftime("%Y-%m-%d 00:00:00")]],
        order="date desc",
        max_rows=DASHBOARD_MAX_MOVES,
    )
    out_by_product = defaultdict(float)
    for move in moves:
        source = first_text(move.get("location_id"))
        if not source.startswith("WH/库存"):
            continue
        pid = first_text(move.get("product_id"))
        qty = float(move.get("product_uom_qty") or 0)
        out_by_product[pid] += qty

    daily_rate = {}
    for pid, total in out_by_product.items():
        rate = round(total / 30.0, 2)
        if rate >= 0.01:
            daily_rate[pid] = {"total30": round(total, 1), "daily": rate}

    return {"dailyRates": daily_rate, "productCount": len(daily_rate)}


def build_product_urgency(stock_snapshot, orderpoints, consumption, pol_data):
    """Compute urgency from product-level stock summed across internal locations."""
    products_in_po = set(pol_data.get("orderProductIds", []))
    purchase_by_product = pol_data.get("byProduct", {})
    daily_rates = consumption.get("dailyRates", {})
    ops_map = orderpoints.get("byProduct", {})

    candidates = {}
    product_names = set(stock_snapshot) | set(ops_map)
    for pid in product_names:
        if not pid or pid == "-":
            continue
        stock_entry = stock_snapshot.get(pid, {})
        qty = float(stock_entry.get("qty") or 0)
        op = ops_map.get(pid, {})
        min_qty = float(op.get("min") or 0)
        qty_to_order = float(op.get("qtyToOrder") or 0)
        below = max(0, min_qty - qty) if min_qty > 0 else 0.0
        if qty > 0 and below <= 0 and qty_to_order <= 0:
            continue

        rate = daily_rates.get(pid, {})
        daily = float(rate.get("daily") or 0)
        has_po = pid in products_in_po
        purchase_info = purchase_by_product.get(pid, {})
        urgency = below + (daily * 7)
        candidates[pid] = {
            "product": pid,
            "qty": round(qty, 2),
            "minQty": min_qty,
            "qtyToOrder": qty_to_order,
            "belowBy": round(below, 1),
            "dailyUse": daily,
            "total30Use": rate.get("total30", 0),
            "hasOpenPO": has_po,
            "openPOState": purchase_info.get("status", ""),
            "openPOQty": round(float(purchase_info.get("remainingQty") or 0), 2),
            "openPONames": purchase_info.get("orders", []),
            "urgency": round(urgency, 1),
        }

    sorted_items = sorted(candidates.values(), key=lambda item: item["urgency"], reverse=True)
    return {
        "items": sorted_items,
        "totalBelowMin": sum(1 for item in sorted_items if item["belowBy"] > 0),
        "totalZeroStock": sum(1 for item in sorted_items if item["qty"] <= 0),
        "totalNoPO": sum(1 for item in sorted_items if not item["hasOpenPO"]),
    }


_CACHE = {"data": None, "ts": 0}
_ODOO_CACHE = {"raw": None, "ts": 0}  # cache only raw Odoo data, merge fresh overrides
_SEARCH_CACHE = {"products": None, "ts": 0, "queries": {}}


def split_product_display(value):
    text = str(value or "").strip()
    if text.startswith("[") and "]" in text:
        code, name = text[1:].split("]", 1)
        return code.strip(), name.strip() or text
    return "", text


def build_product_search_cache():
    client = OdooClient()
    client.authenticate()
    fields = [
        "display_name",
        "default_code",
        "name",
        "barcode",
        "uom_id",
        "categ_id",
        "qty_available",
        "virtual_available",
        "incoming_qty",
        "outgoing_qty",
    ]
    try:
        rows = client.search_read_all(
            "product.product",
            fields,
            [["active", "=", True]],
            order="default_code asc, name asc",
            page_size=500,
        )
    except OdooError:
        fields = ["display_name", "default_code", "name", "barcode", "uom_id", "categ_id"]
        rows = client.search_read_all(
            "product.product",
            fields,
            [["active", "=", True]],
            order="default_code asc, name asc",
            page_size=500,
        )

    orderpoints = build_orderpoints(client)
    ops_map = orderpoints.get("byProduct", {})
    products = []
    for row in rows:
        display = str(row.get("display_name") or row.get("name") or "").strip()
        parsed_code, parsed_name = split_product_display(display)
        code = str(row.get("default_code") or parsed_code or "").strip()
        name = str(row.get("name") or parsed_name or display).strip()
        op_key = display if display in ops_map else find_matching_product(display, ops_map.keys())
        op = ops_map.get(op_key, {}) if op_key else {}
        item = {
            "id": row.get("id"),
            "product": display or name or code,
            "code": code,
            "name": name,
            "barcode": row.get("barcode") or "",
            "uom": first_text(row.get("uom_id")),
            "category": first_text(row.get("categ_id")),
            "qtyAvailable": float(row.get("qty_available") or 0),
            "virtualAvailable": float(row.get("virtual_available") or 0),
            "incomingQty": float(row.get("incoming_qty") or 0),
            "outgoingQty": float(row.get("outgoing_qty") or 0),
            "minQty": float(op.get("min") or 0),
            "qtyToOrder": float(op.get("qtyToOrder") or 0),
        }
        item["_search"] = product_key(
            " ".join([
                str(item["product"]),
                str(item["code"]),
                str(item["name"]),
                str(item["barcode"]),
                str(item["category"]),
            ])
        )
        products.append(item)
    return products


def search_products(query, limit=50):
    text = str(query or "").strip()
    if not product_key(text):
        return []

    client = OdooClient()
    client.authenticate()
    fields = [
        "display_name",
        "default_code",
        "name",
        "barcode",
        "uom_id",
        "categ_id",
        "qty_available",
        "virtual_available",
        "incoming_qty",
        "outgoing_qty",
    ]
    domain = [
        "|", "|",
        ["default_code", "ilike", text],
        ["name", "ilike", text],
        ["barcode", "ilike", text],
    ]
    try:
        rows = client.search_read("product.product", fields, [["active", "=", True], *domain], limit=limit, order="default_code asc, name asc")
    except OdooError:
        rows = client.search_read("product.product", fields[:6], [["active", "=", True], *domain], limit=limit, order="default_code asc, name asc")

    results = []
    for row in rows:
        display = str(row.get("display_name") or row.get("name") or "").strip()
        parsed_code, parsed_name = split_product_display(display)
        code = str(row.get("default_code") or parsed_code or "").strip()
        name = str(row.get("name") or parsed_name or display).strip()
        results.append({
            "id": row.get("id"),
            "product": display or name or code,
            "code": code,
            "name": name,
            "barcode": row.get("barcode") or "",
            "uom": first_text(row.get("uom_id")),
            "category": first_text(row.get("categ_id")),
            "qtyAvailable": float(row.get("qty_available") or 0),
            "virtualAvailable": float(row.get("virtual_available") or 0),
            "incomingQty": float(row.get("incoming_qty") or 0),
            "outgoingQty": float(row.get("outgoing_qty") or 0),
            "minQty": 0,
            "qtyToOrder": 0,
        })
    return results

def build_dashboard(nocache=False):
    import time as _time
    # Return cached full data only if fresh and no nocache
    if not nocache and _CACHE["data"] is not None and _time.time() - _CACHE["ts"] < CACHE_TTL_SECONDS:
        return _CACHE["data"]

    client = OdooClient()
    session_info = client.authenticate()

    # Core data
    warehouse_data = build_warehouse(client)
    purchase_data = build_purchase(client)

    # Reuse quants from warehouse_data (single source of truth)
    internal_quants = warehouse_data.pop("_internalQuants", [])
    zero_quants = warehouse_data.pop("_zeroQuants", [])

    # Update zeroStock KPI using the exact same data
    if "warehouseKpis" in warehouse_data:
        warehouse_data["warehouseKpis"]["zeroStock"] = len(zero_quants)
        warehouse_data["warehouseKpis"]["productCount"] = len({first_text(q.get("product_id")) for q in internal_quants}) or len(internal_quants)
        warehouse_data["warehouseKpis"]["stockSum"] = sum(float(q.get("quantity") or 0) for q in internal_quants)

    # Purchase lines + procurement gap
    pol_data = build_purchase_lines(client)
    gap_data = build_procurement_gap(zero_quants, pol_data)

    # Supplier contacts
    all_orders = client.search_read_all("purchase.order", ["partner_id"], [], order="id desc", max_rows=DASHBOARD_MAX_ORDERS)
    contact_map = build_supplier_contacts(client, all_orders)

    # Reorder rules, replenishment list, consumption rates, product urgency
    orderpoints = build_orderpoints(client)
    replenishment_list = build_replenishment_list(client)
    consumption = build_consumption_rates(client)
    stock_snapshot = build_stock_snapshot(internal_quants, orderpoints)
    urgency_data = build_product_urgency(stock_snapshot, orderpoints, consumption, pol_data)
    manual_urgent_items = enrich_manual_urgent_items(
        pol_data.get("orderProductIds", []),
        stock_snapshot,
        pol_data.get("byProduct", {}),
    )
    supplier_product_names = [item.get("product", "") for item in urgency_data.get("items", [])]
    supplier_product_names.extend(item.get("product", "") for item in replenishment_list.get("items", []))
    product_suppliers = build_product_suppliers(client, supplier_product_names)

    data = {}
    data.update(warehouse_data)
    data.update(purchase_data)
    data["procurementGap"] = gap_data
    data["purchaseLines"] = pol_data
    data["supplierContacts"] = contact_map
    data["productSuppliers"] = product_suppliers
    data["orderpoints"] = orderpoints
    data["replenishmentList"] = replenishment_list
    data["consumption"] = consumption
    data["productUrgency"] = urgency_data
    # Add product prices — match by integer product ID from zero quants
    pid_to_name = {}
    for q in zero_quants:
        raw = q.get("product_id")
        if isinstance(raw, list) and len(raw) > 0:
            pid_to_name[int(raw[0])] = first_text(raw)
    price_map = {}
    if pid_to_name:
        pids = list(pid_to_name.keys())
        batch_size = 200
        for i in range(0, len(pids), batch_size):
            batch = pids[i:i+batch_size]
            try:
                rows = client.search_read_all(
                    "product.product",
                    ["standard_price"],
                    [["id", "in", batch]],
                    page_size=batch_size,
                )
                for row in rows:
                    price_map[int(row["id"])] = float(row.get("standard_price") or 0)
            except OdooError:
                pass
    for item in urgency_data.get("items", []):
        item["standardPrice"] = 0
    for item in urgency_data.get("items", []):
        for pid, pname in pid_to_name.items():
            if pname == item.get("product", ""):
                item["standardPrice"] = price_map.get(pid, 0)
                break
    # Cleanup resolved items first, then load tracking
    auto_cleanup(stock_snapshot, orderpoints)
    data["tracking"] = load_tracking()

    # Merge overrides into urgency data
    overrides = load_overrides()
    tracked_products = set()
    for req in load_tracking().get("requests", {}).values():
        tracked_products.add(req.get("product", ""))
    urgency_items = data.get("productUrgency", {}).get("items", [])
    existing_products = {item.get("product", "") for item in urgency_items}
    stock_names = list(stock_snapshot.keys())
    def override_for_product(product):
        override = overrides.get(product)
        if override:
            return override
        matched = find_matching_product(product, list(overrides.keys()))
        return overrides.get(matched, {}) if matched else {}

    if overrides and urgency_items:
        urgency_items = [
            item for item in urgency_items
            if not is_removed_override(override_for_product(item.get("product", "")))
        ]
        data["productUrgency"]["items"] = urgency_items
        existing_products = {item.get("product", "") for item in urgency_items}

    # Merge overrides into existing urgency items
    if overrides and urgency_items:
        for item in urgency_items:
            ov = override_for_product(item.get("product", ""))
            if ov:
                if ov.get("qty") is not None and ov["qty"] != item.get("qty"):
                    item["_odooQty"] = item.get("qty")
                    item["qty"] = ov["qty"]
                if ov.get("minQty") is not None and ov["minQty"] != item.get("minQty"):
                    item["_odooMinQty"] = item.get("minQty")
                    item["minQty"] = ov["minQty"]
                if ov.get("qtyToOrder") is not None and ov["qtyToOrder"] != item.get("qtyToOrder"):
                    item["_odooQtyToOrder"] = item.get("qtyToOrder")
                    item["qtyToOrder"] = ov["qtyToOrder"]
                item["_overridden"] = True
    # Add search-added products (in overrides but not in urgency list)
    for product, ov in overrides.items():
        if is_removed_override(ov):
            continue
        matched = find_matching_product(product, list(existing_products))
        if matched:
            continue  # Already matched above
        stock_entry = stock_snapshot.get(product, {})
        if not stock_entry:
            m = find_matching_product(product, stock_names)
            stock_entry = stock_snapshot.get(m, {})
        qty = float(ov.get("qty") or stock_entry.get("qty") or 0)
        min_q = float(ov.get("minQty") or stock_entry.get("minQty") or 0)
        qty_order = float(ov.get("qtyToOrder") or 0)
        urgency_items.insert(0, {
            "product": product,
            "qty": qty,
            "minQty": min_q,
            "qtyToOrder": qty_order,
            "dailyUse": 0,
            "belowBy": max(min_q - qty, 0),
            "hasOpenPO": product in pol_data.get("orderProductIds", []),
            "urgency": 0,
            "_overridden": True,
            "_searchAdded": True,
        })
    # Sort urgency: search-added first, then tracked, then by urgency
    if urgency_items:
        def _sort_key(item):
            is_search = 0 if item.get("_searchAdded") else 1
            is_tracked = 0 if item.get("product") in tracked_products else 1
            urgency = -(item.get("urgency", 0) or 0)
            return (is_search, is_tracked, urgency)
        urgency_items.sort(key=_sort_key)
        data["productUrgency"]["items"] = urgency_items
        data["productUrgency"]["totalBelowMin"] = sum(1 for item in urgency_items if float(item.get("belowBy") or 0) > 0)
        data["productUrgency"]["totalZeroStock"] = sum(1 for item in urgency_items if float(item.get("qty") or 0) <= 0)
        data["productUrgency"]["totalNoPO"] = sum(1 for item in urgency_items if not item.get("hasOpenPO"))
    data["manualUrgentItems"] = manual_urgent_items
    data["orderpoints"] = orderpoints
    data["meta"] = {
        "source": "odoo",
        "db": ODOO_DB,
        "user": session_info.get("username") or ODOO_USER,
        "serverVersion": session_info.get("server_version"),
        "updatedAt": datetime.now().isoformat(timespec="seconds"),
    }
    _CACHE["data"] = data
    _CACHE["ts"] = _time.time()
    return data


def build_replenishments():
    client = OdooClient()
    session_info = client.authenticate()
    replenishment_list = build_replenishment_list(client)
    return {
        "replenishmentList": replenishment_list,
        "meta": {
            "source": "odoo",
            "db": ODOO_DB,
            "user": session_info.get("username") or ODOO_USER,
            "serverVersion": session_info.get("server_version"),
            "updatedAt": datetime.now().isoformat(timespec="seconds"),
        },
    }


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/dashboard":
            import urllib.parse as _up
            qs = _up.parse_qs(parsed.query)
            nocache = "nocache" in qs
            self.send_json_response(nocache=nocache)
            return
        if path == "/api/replenishments":
            self.send_replenishments_response()
            return
        if path == "/api/health":
            self.write_json({"ok": True, "source": "local"})
            return
        if path == "/api/tracking":
            self.write_json({"ok": True, "tracking": load_tracking()})
            return
        if path == "/api/manual-urgent":
            self.write_json({"ok": True, "items": load_manual_urgent_items()})
            return
        super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            payload = self.read_json_body()
            if path == "/api/products/search":
                q = str(payload.get("q") or "").strip()
                limit = int(payload.get("limit") or 50)
                self.write_json({"ok": True, "results": search_products(q, limit=max(1, min(limit, 100)))})
                return
            if path == "/api/replenishments/purchased":
                record_id = str(payload.get("id") or "").strip()
                if not record_id:
                    raise ValueError("缺少补货记录 ID")
                self.write_json({"ok": True, "data": mark_replenishment_purchased(record_id)})
                return            self.write_json({
                "ok": False,
                "error": "只读异常看板不提供新增、删除、修改、审批、入库或写回 Odoo 的接口",
            })
        except Exception as exc:
            self.write_json({"ok": False, "error": str(exc)})

    def read_json_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}")

    def send_json_response(self, nocache=False):
        try:
            payload = {"ok": True, "data": build_dashboard(nocache=nocache)}
        except (OdooError, URLError, TimeoutError) as exc:
            payload = {"ok": False, "error": str(exc)}
        except Exception as exc:
            payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        self.write_json(payload)

    def send_replenishments_response(self):
        try:
            payload = {"ok": True, "data": build_replenishments()}
        except (OdooError, URLError, TimeoutError) as exc:
            payload = {"ok": False, "error": str(exc)}
        except Exception as exc:
            payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        self.write_json(payload)

    def write_json(self, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    port = int(os.getenv("PORT", "8766"))
    server = ThreadingHTTPServer(("0.0.0.0", port), DashboardHandler)
    print(f"ERP dashboard server: http://127.0.0.1:{port}")
    print(f"Odoo: {ODOO_URL} db={ODOO_DB} user={ODOO_USER}")
    server.serve_forever()


if __name__ == "__main__":
    main()
