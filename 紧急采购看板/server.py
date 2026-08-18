# -*- coding: utf-8 -*-
"""
紧急采购看板 —— 后端服务（只读）

数据源：Odoo 中标有「紧急」且尚未转为采购订单（未采购）的采购单 / 询价单。
本服务只调用 Odoo 只读方法（search_read / search_count / fields_get / read），
不提供任何新增、修改、删除、审批或写回 Odoo 的接口。

环境变量：
  ODOO_URL              Odoo 地址           默认 http://x.inspiri.cn
  ODOO_DB               Odoo 数据库         默认 inspiri_erp
  ODOO_USER             Odoo 登录用户       必填
  ODOO_PASSWORD         Odoo 登录密码       必填
  ODOO_URGENT_FIELD     紧急标记字段名       默认自动探测 x_studio_urgent / x_urgent / urgent / priority
  ODOO_URGENT_TAG       紧急标签文本         默认 紧急（字段为文本/选择型时按此匹配）
  ODOO_URGENT_DOMAIN    完整紧急过滤域(JSON) 可选，配置后优先于字段探测
  ODOO_URGENT_STATES    未采购状态(JSON)     默认 ["draft","sent","to approve"]
  PORT                  服务端口             默认 8766
  CACHE_TTL_SECONDS     数据缓存秒数         默认 180
"""

import json
import os
import statistics
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from http import HTTPStatus
from http.cookiejar import CookieJar
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener

BASE_DIR = Path(__file__).resolve().parent


def load_env_file(path=None):
    """从 .env 文件加载环境变量（标准库实现，不覆盖已存在的环境变量）。

    .env 格式：KEY=VALUE，支持 # 注释；值可带单/双引号。
    已由系统/命令行设置的环境变量优先，不会被 .env 覆盖。
    """
    env_path = Path(path) if path else BASE_DIR / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value


load_env_file()

ODOO_URL = os.getenv("ODOO_URL", "http://x.inspiri.cn").rstrip("/")
# 供前端「在 Odoo 打开」跳转使用的浏览器侧地址；未配置时回退 ODOO_URL。
# 用于生成深链 <ODOO_WEB_URL>/odoo/purchase.order/{id}，跳到对应采购单表单。
ODOO_WEB_URL = os.getenv("ODOO_WEB_URL", "").strip().rstrip("/") or ODOO_URL
ODOO_DB = os.getenv("ODOO_DB", "inspiri_erp")
ODOO_USER = os.getenv("ODOO_USER", "")
ODOO_PASSWORD = os.getenv("ODOO_PASSWORD", "")


def _env_int(name, default, minimum=None):
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        print(f"⚠️  环境变量 {name} 不是合法整数（当前值：{os.getenv(name)!r}），已使用默认值 {default}")
        value = default
    if minimum is not None and value < minimum:
        value = default
    return value


CACHE_TTL_SECONDS = _env_int("CACHE_TTL_SECONDS", 180, minimum=5)
DASHBOARD_MAX_ORDERS = _env_int("DASHBOARD_MAX_ORDERS", 500, minimum=1)

URGENT_FIELD_CANDIDATES = [
    "x_studio_urgent",
    "x_urgent",
    "urgent",
    "x_studio_is_urgent",
    "x_studio_priority",
    "priority",
]
URGENT_TAG = os.getenv("ODOO_URGENT_TAG", "紧急").strip()
URGENT_FIELD = os.getenv("ODOO_URGENT_FIELD", "").strip()
URGENT_DOMAIN_OVERRIDE = os.getenv("ODOO_URGENT_DOMAIN", "").strip()
try:
    URGENT_STATES = json.loads(os.getenv("ODOO_URGENT_STATES", '["draft","sent","to approve"]'))
    if not isinstance(URGENT_STATES, list):
        raise ValueError("必须是 JSON 数组")
except (ValueError, json.JSONDecodeError):
    print("⚠️  环境变量 ODOO_URGENT_STATES 不是合法的 JSON 数组，已使用默认值 ['draft','sent','to approve']")
    URGENT_STATES = ["draft", "sent", "to approve"]

READ_ONLY_METHODS = {"search_read", "search_count", "read", "fields_get", "name_search", "search"}

# 可选访问令牌：设置 BOARD_ACCESS_TOKEN 后，/api/* 请求必须携带
#   ?token=<BOARD_ACCESS_TOKEN>  或  Authorization: Bearer <BOARD_ACCESS_TOKEN>
# 留空则不限（适合纯内网可信环境）。
_ACCESS_TOKEN = os.getenv("BOARD_ACCESS_TOKEN", "").strip()

# 前端静态文件白名单：只有这些文件允许被浏览器访问，
# 其余（.env / server.py / *.log / *.pyc / README 等）一律 404，防止敏感信息泄露。
STATIC_FILE_WHITELIST = {
    "/",
    "/index.html",
    "/styles.css",
    "/app.js",
}

STATE_LABELS = {
    "draft": "询价单",
    "sent": "已发送",
    "to approve": "待审批",
    "purchase": "采购订单",
    "done": "已完成",
    "cancel": "已取消",
}

STATE_COLORS = {
    "draft": "#ffbf4d",
    "sent": "#18d8ff",
    "to approve": "#ff6274",
    "purchase": "#23e0b2",
    "done": "#23e0b2",
    "cancel": "#64748b",
}

_CACHE = {"data": None, "ts": 0}
_CACHE_LOCK = threading.Lock()
_REFRESHING = False


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
    if isinstance(value, str):
        # Odoo 不同版本可能返回 "2026-08-07 01:10:59" 或 "2026-08-07T01:10:59" 或带毫秒
        value = value.strip()
        if "T" in value:
            value = value.replace("T", " ")
        if "." in value:
            value = value.split(".")[0]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


def money(value):
    return f"¥{float(value or 0):,.2f}"


def now_text():
    return datetime.now().isoformat(timespec="seconds")


def status_label(state):
    return STATE_LABELS.get(state, state or "-")


def parse_material(product):
    """Split '[CODE] NAME' display into code and name."""
    text = str(product or "").strip()
    if text.startswith("[") and "]" in text:
        code, _, name = text.partition("]")
        return code[1:].strip(), (name.strip() or text)
    return "", text


def resolve_urgent_domain(client):
    """Build the 'urgent' domain part for purchase.order (read-only)."""
    if URGENT_DOMAIN_OVERRIDE:
        try:
            return json.loads(URGENT_DOMAIN_OVERRIDE)
        except json.JSONDecodeError as exc:
            raise OdooError(f"ODOO_URGENT_DOMAIN 不是合法的 JSON: {exc}") from exc

    field = URGENT_FIELD
    model_fields = {}
    try:
        model_fields = client.call_kw(
            "purchase.order", "fields_get", [], {"attributes": ["type", "selection"]}
        )
    except OdooError:
        model_fields = {}

    if not field:
        for candidate in URGENT_FIELD_CANDIDATES:
            if candidate in model_fields:
                field = candidate
                break

    if field:
        ftype = model_fields.get(field, {}).get("type", "boolean")
        if ftype == "boolean":
            return [[field, "=", True]]
        if ftype == "selection":
            # selection 类型：存储值是选项 key（如 priority 的 "0"/"1"），
            # 必须按选项 label 匹配「紧急 / urgent」取出对应 key 再过滤。
            options = model_fields.get(field, {}).get("selection") or []
            keys = [
                str(key)
                for key, label in options
                if URGENT_TAG.lower() in str(label or "").lower()
                or "urgent" in str(label or "").lower()
            ]
            if keys:
                return [[field, "in", keys]]
            # 选项无匹配：回退到文本/布尔匹配
            return [
                "|", "|",
                [field, "ilike", URGENT_TAG],
                [field, "ilike", "urgent"],
                [field, "=", True],
            ]
        # 选择 / 文本 / 字符型：值可能是 紧急 / urgent / 是 / true
        return [
            "|", "|",
            [field, "ilike", URGENT_TAG],
            [field, "ilike", "urgent"],
            [field, "=", True],
        ]

    # 未找到紧急字段：兜底按单号包含「紧急」识别
    return [["name", "ilike", URGENT_TAG]]


def order_level(days_overdue, days_to_planned):
    """P0 今天必须处理 / P1 3天内 / P2 本周 / P3 普通提醒。"""
    if days_overdue > 0 or days_to_planned == 0:
        return "P0"
    if days_to_planned <= 3:
        return "P1"
    if days_to_planned <= 7:
        return "P2"
    return "P3"


def build_urgent_orders(client):
    now = datetime.now(timezone.utc)
    states = [s for s in URGENT_STATES if s]
    state_domain = [["state", "in", states]] if states else []
    urgent_domain = resolve_urgent_domain(client)
    domain = ["&", *state_domain, *urgent_domain] if state_domain else urgent_domain

    fields = [
        "id", "name", "partner_id", "user_id", "amount_total", "state",
        "date_order", "date_planned", "currency_id",
    ]
    orders = client.search_read_all(
        "purchase.order",
        fields,
        domain,
        order="date_planned asc, id desc",
        max_rows=DASHBOARD_MAX_ORDERS,
    )

    order_ids = [o["id"] for o in orders if o.get("id")]
    lines_by_order = defaultdict(list)
    if order_ids:
        line_rows = client.search_read_all(
            "purchase.order.line",
            ["order_id", "product_id", "name", "product_qty", "qty_received",
             "price_unit", "product_uom", "state", "note"],
            [["order_id", "in", order_ids]],
            order="id asc",
            max_rows=DASHBOARD_MAX_ORDERS * 8,
        )
        for row in line_rows:
            raw = row.get("order_id")
            oid = raw[0] if isinstance(raw, list) and raw else None
            if oid in order_ids:
                lines_by_order[oid].append(row)

    items = []
    for o in orders:
        order_id = o.get("id")
        date_order = parse_dt(o.get("date_order"))
        date_planned = parse_dt(o.get("date_planned"))
        state = str(o.get("state") or "")

        days_waiting = (now.date() - date_order.date()).days if date_order else 0
        days_overdue = 0
        days_to_planned = 999
        if date_planned:
            delta = (date_planned.date() - now.date()).days
            if delta < 0:
                days_overdue = abs(delta)
            else:
                days_to_planned = delta

        lines = []
        for row in lines_by_order.get(order_id, []):
            product = first_text(row.get("product_id"))
            ordered = float(row.get("product_qty") or 0)
            received = float(row.get("qty_received") or 0)
            lines.append({
                "product": product,
                "name": str(row.get("name") or "").strip(),
                "qty": ordered,
                "received": received,
                "remaining": round(max(ordered - received, 0), 4),
                "price": float(row.get("price_unit") or 0),
                "uom": first_text(row.get("product_uom")),
                "state": str(row.get("state") or ""),
                "note": str(row.get("note") or "").strip(),
            })

        material_names = []
        material_count = 0
        for line in lines:
            code, name = parse_material(line.get("product"))
            display = name or code or line.get("product")
            if display and display != "-" and display not in material_names:
                material_names.append(display)
        material_count = len(material_names)

        items.append({
            "id": order_id,
            "name": first_text(o.get("name")),
            "supplier": first_text(o.get("partner_id")),
            "buyer": first_text(o.get("user_id"), ""),
            "amount": float(o.get("amount_total") or 0),
            "amountText": money(o.get("amount_total")),
            "state": state,
            "stateText": status_label(state),
            "dateOrder": o.get("date_order") or "",
            "datePlanned": o.get("date_planned") or "",
            "daysWaiting": max(days_waiting, 0),
            "daysOverdue": days_overdue,
            "daysToPlanned": days_to_planned if days_to_planned < 999 else None,
            "level": order_level(days_overdue, days_to_planned),
            "materials": material_names[:3],
            "materialCount": material_count,
            "lines": lines,
            "plannedText": (
                f"超期 {days_overdue} 天" if days_overdue > 0
                else "今天到期" if date_planned and days_to_planned == 0
                else f"{days_to_planned} 天后到期" if date_planned and days_to_planned < 999
                else "未设置预计日期"
            ),
        })

    # 汇总指标
    total = len(items)
    today = sum(1 for item in items if item["level"] == "P0")
    overdue = sum(1 for item in items if item["daysOverdue"] > 0)
    amount_sum = sum(item["amount"] for item in items)
    suppliers = len({item["supplier"] for item in items if item["supplier"] != "-"})
    waiting_days = [item["daysWaiting"] for item in items if item["daysWaiting"] > 0]
    avg_waiting = round(statistics.mean(waiting_days), 1) if waiting_days else 0

    # 状态分布
    state_totals = defaultdict(int)
    for item in items:
        state_totals[item["stateText"]] += 1
    state_rows = [
        [name, count, STATE_COLORS.get(state, "#18d8ff")]
        for state, name in STATE_LABELS.items()
        for count in [state_totals.get(name, 0)]
        if count > 0
    ]

    # 供应商排行（按金额）
    supplier_totals = defaultdict(lambda: {"amount": 0.0, "count": 0})
    for item in items:
        supplier = item["supplier"] if item["supplier"] != "-" else "未指定供应商"
        supplier_totals[supplier]["amount"] += item["amount"]
        supplier_totals[supplier]["count"] += 1
    supplier_rows = [
        [name, money(info["amount"]), f"{info['count']} 单"]
        for name, info in sorted(supplier_totals.items(), key=lambda kv: kv[1]["amount"], reverse=True)[:8]
    ]

    summary = [
        f"当前共有 {total} 条标有「紧急」的未采购订单/询价单，其中 {today} 条已超期或今天到期（P0）。",
        f"已超期 {overdue} 条，平均等待 {avg_waiting} 天，涉及 {suppliers} 家供应商，合计金额 {money(amount_sum)}。",
        "数据只读展示：Odoo 中把采购单标记为紧急、或确认转成采购订单后，下一次刷新会自动更新。",
    ]

    return {
        "kpis": {
            "total": total,
            "today": today,
            "overdue": overdue,
            "amount": money(amount_sum),
            "suppliers": suppliers,
            "avgWaiting": avg_waiting,
        },
        "orders": items,
        "states": state_rows,
        "suppliers": supplier_rows,
        "summary": summary,
    }


def build_dashboard(nocache=False):
    global _REFRESHING
    now = time.time()
    # 缓存命中：非强制刷新且缓存未过期
    if not nocache and _CACHE["data"] is not None and now - _CACHE["ts"] < CACHE_TTL_SECONDS:
        return _CACHE["data"]

    # 已有线程在刷新：先等待其完成（不能持锁等待，否则会死锁）
    if _REFRESHING:
        for _ in range(80):  # 最多等 20 秒
            time.sleep(0.25)
            if not _REFRESHING:
                break
        if _CACHE["data"] is not None:
            return _CACHE["data"]

    with _CACHE_LOCK:
        if _REFRESHING:
            # 双检：等待期间其他线程刚完成刷新，直接复用结果
            return _CACHE["data"]
        _REFRESHING = True

    try:
        client = OdooClient()
        client.authenticate()
        data = build_urgent_orders(client)
        # 注意：此处刻意不返回 Odoo 数据库名 / 登录账号 / Odoo 版本号等内部信息，
        # 避免局域网内任何人访问 API 即获取服务器指纹。
        data["meta"] = {
            "source": "odoo",
            "updatedAt": datetime.now().isoformat(timespec="seconds"),
            # 只暴露 Odoo 的访问地址（用于跳转到对应采购单），不暴露数据库名/账号等内部信息
            "odooWebUrl": ODOO_WEB_URL,
        }
        _CACHE["data"] = data
        _CACHE["ts"] = time.time()
        return data
    finally:
        with _CACHE_LOCK:
            _REFRESHING = False


class DashboardHandler(SimpleHTTPRequestHandler):
    # 隐藏默认的 "SimpleHTTP/0.6 Python/3.13.x" 服务器指纹头
    server_version = "PurchasingBoard"
    sys_version = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def end_headers(self):
        # 静态资源默认 no-cache，避免浏览器缓存旧版本（改 CSS/JS 后普通刷新即可生效）
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        # 同源部署：前端由本服务提供，无需跨域。去掉 CORS * 可阻止恶意网页跨域读取看板数据。
        # 如需局域网内其他站点调用 API，请按实际来源域名配置 Access-Control-Allow-Origin。
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def _authorized(self):
        """访问令牌校验：未配置令牌时放行；配置后校验 query token 或 Authorization 头。"""
        if not _ACCESS_TOKEN:
            return True
        import urllib.parse as _up
        qs = _up.parse_qs(urlparse(self.path).query)
        if qs.get("token", [None])[0] == _ACCESS_TOKEN:
            return True
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and auth[len("Bearer "):].strip() == _ACCESS_TOKEN:
            return True
        return False

    def do_GET(self):
        import urllib.parse as _up
        path = urlparse(self.path).path
        if path in {"/api/urgent-orders", "/api/health"}:
            if not self._authorized():
                self.send_error(HTTPStatus.UNAUTHORIZED, "Unauthorized")
                return
            if path == "/api/urgent-orders":
                qs = _up.parse_qs(urlparse(self.path).query)
                nocache = "nocache" in qs
                self.send_dashboard_response(nocache=nocache)
            else:
                self.write_json({"ok": True, "source": "local"})
            return
        # 静态文件白名单：只放行前端必需文件，其余（.env / server.py / 日志等）一律 404
        if path in STATIC_FILE_WHITELIST:
            super().do_GET()
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def send_dashboard_response(self, nocache=False):
        try:
            payload = {"ok": True, "data": build_dashboard(nocache=nocache)}
        except (OdooError, URLError, TimeoutError) as exc:
            # 只向前端暴露可读的错误概要；不包含 Odoo 内部路径/堆栈
            message = str(exc)
            if len(message) > 160:
                message = message[:160] + "…"
            payload = {"ok": False, "error": message}
        except Exception as exc:
            print(f"[ERROR] dashboard 构建失败: {type(exc).__name__}: {exc}", file=sys.stderr)
            payload = {"ok": False, "error": "内部错误，请查看服务端日志"}
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
    host = os.getenv("HOST", "127.0.0.1").strip() or "127.0.0.1"
    # 默认只监听本机回环地址，避免局域网任意机器直接访问看板数据；
    # 如需局域网共享，请显式设置 HOST=0.0.0.0（并考虑配合访问令牌 BOARD_ACCESS_TOKEN）。
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    print(f"紧急采购看板服务: http://127.0.0.1:{port}")
    print(f"监听地址: {host}:{port}  |  Odoo: {ODOO_URL}  db={ODOO_DB}")
    print(f"紧急标记: 字段={URGENT_FIELD or '自动探测'} 标签={URGENT_TAG} 未采购状态={URGENT_STATES}")
    if _ACCESS_TOKEN:
        print("访问令牌: 已启用（API 需携带令牌）")
    else:
        print("访问令牌: 未启用（建议内网使用，勿暴露到公网）")
    server.serve_forever()


if __name__ == "__main__":
    main()
