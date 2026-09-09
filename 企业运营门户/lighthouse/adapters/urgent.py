"""Legacy urgent purchase list grouping and colour rules, read only."""
import json, re, statistics
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from lighthouse.odoo import OdooError
LOCAL_TZ = timezone(timedelta(hours=8))
DASHBOARD_MAX_ORDERS = 500
URGENT_STATES = ['draft','sent','to approve']
STATE_LABELS = {'draft':'询价单','sent':'已发送','to approve':'待审批','purchase':'采购订单','done':'已完成','cancel':'已取消'}
LIST_LEVEL_ORDER = {'P0':0,'P1':1,'P2':2,'P3':3}
LIST_CATEGORY_RULES = [('立体仓储',['立体仓储','立体库','立库','asrs']),('RGV',['rgv']),('堆垛机',['堆垛机','堆垛','stacker'])]
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

def parse_list_name(origin):
    """从采购单 source document (origin) 提取「清单」名称。

    Odoo 的 origin 形如 "清单:150KG堆垛机电气清单"（也可能是 "SO001, 清单:xxx" 这类复合来源），
    这里去掉「清单: / 清单：」前缀返回纯清单名；无来源或来源不是清单（如直接是销售单号 "S00124"）
    时返回「未关联清单」。
    """
    text = str(origin or "").strip()
    if not text:
        return "未关联清单"
    match = re.search(r"清单\s*[:：]\s*(.+)$", text)
    if match:
        return match.group(1).strip() or text
    return "未关联清单"

def classify_list_category(list_name):
    """按清单名关键词把清单归类到「板块」。

    命中内置关键词（立体仓储 / RGV / 堆垛机 …）→ 归入对应板块；
    未命中（含「未关联清单」这类无清单归属的单据）→ 统一归入「其他」。
    """
    name = str(list_name or "").strip()
    text = name.lower()
    for category, keywords in LIST_CATEGORY_RULES:
        for kw in keywords:
            if kw.lower() in text:
                return category
    return "其他"

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
    now = datetime.now(LOCAL_TZ)
    states = [s for s in URGENT_STATES if s]
    state_domain = [["state", "in", states]] if states else []
    urgent_domain = resolve_urgent_domain(client)
    domain = ["&", *state_domain, *urgent_domain] if state_domain else urgent_domain

    fields = [
        "id", "name", "partner_id", "user_id", "amount_total", "state",
        "date_order", "date_planned", "currency_id", "origin",
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

        days_waiting = (now.date() - date_order.astimezone(LOCAL_TZ).date()).days if date_order else 0
        days_overdue = 0
        days_to_planned = 999
        if date_planned:
            delta = (date_planned.astimezone(LOCAL_TZ).date() - now.date()).days
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
            "origin": str(o.get("origin") or "").strip(),
            "list": parse_list_name(o.get("origin")),
            "category": classify_list_category(parse_list_name(o.get("origin"))),
            "supplier": first_text(o.get("partner_id")),
            "buyer": first_text(o.get("user_id"), ""),
            "amount": float(o.get("amount_total") or 0),
            "currency": first_text(o.get('currency_id'), '币种未提供'),
            "amountText": f"{first_text(o.get('currency_id'), '币种未提供')} {float(o.get('amount_total') or 0):,.2f}",
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

    # 清单分组：同一清单下的多张采购单归为一组，看板以清单为主展示
    lists_map = {}
    for item in items:
        key = item["list"]
        group = lists_map.get(key)
        if group is None:
            group = {
                "name": key,
                "origin": item["origin"],
                "category": item["category"],
                "orderCount": 0,
                "amount": 0.0,
                "suppliers": set(),
                "levels": {"P0": 0, "P1": 0, "P2": 0, "P3": 0},
                "maxDaysOverdue": 0,
                "minDaysToPlanned": None,
                "orderIds": [],
            }
            lists_map[key] = group
        group["orderCount"] += 1
        group["amount"] += item["amount"]
        if item["supplier"] != "-":
            group["suppliers"].add(item["supplier"])
        group["levels"][item["level"]] += 1
        if item["daysOverdue"] > group["maxDaysOverdue"]:
            group["maxDaysOverdue"] = item["daysOverdue"]
        dtp = item["daysToPlanned"]
        if dtp is not None and (group["minDaysToPlanned"] is None or dtp < group["minDaysToPlanned"]):
            group["minDaysToPlanned"] = dtp
        group["orderIds"].append(item["id"])

    lists = []
    for group in lists_map.values():
        level = min(
            (lv for lv, count in group["levels"].items() if count > 0),
            key=lambda lv: LIST_LEVEL_ORDER[lv],
            default="P3",
        )
        if group["maxDaysOverdue"] > 0:
            urgent_hint = f"最紧急：超期 {group['maxDaysOverdue']} 天"
        elif group["minDaysToPlanned"] == 0:
            urgent_hint = "最紧急：今天到期"
        elif group["minDaysToPlanned"] is not None:
            urgent_hint = f"最紧急：{group['minDaysToPlanned']} 天后到期"
        else:
            urgent_hint = "未设置预计日期"
        lists.append({
            "name": group["name"],
            "origin": group["origin"],
            "category": group["category"],
            "orderCount": group["orderCount"],
            "amount": round(group["amount"], 2),
            "amountText": money(group["amount"]),
            "supplierCount": len(group["suppliers"]),
            "level": level,
            "levels": group["levels"],
            "maxDaysOverdue": group["maxDaysOverdue"],
            "urgentHint": urgent_hint,
            "orderIds": group["orderIds"],
        })
    lists.sort(key=lambda g: (
        LIST_LEVEL_ORDER.get(g["level"], 9),
        -g["maxDaysOverdue"],
        -g["amount"],
    ))

    # 汇总指标
    total = len(items)
    today = sum(1 for item in items if item["level"] == "P0")
    overdue = sum(1 for item in items if item["daysOverdue"] > 0)
    amount_sum = sum(item["amount"] for item in items)
    suppliers = len({item["supplier"] for item in items if item["supplier"] != "-"})
    waiting_days = [item["daysWaiting"] for item in items if item["daysWaiting"] > 0]
    avg_waiting = round(statistics.mean(waiting_days), 1) if waiting_days else 0

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

    # 板块统计（立体仓储 / RGV / 堆垛机 …）
    category_map = {}
    for item in items:
        cat = item["category"]
        c = category_map.setdefault(cat, {
            "listCount": 0, "orderCount": 0, "amount": 0.0,
            "levels": {"P0": 0, "P1": 0, "P2": 0, "P3": 0},
        })
        c["orderCount"] += 1
        c["amount"] += item["amount"]
        c["levels"][item["level"]] += 1
    for l in lists:
        c = category_map.setdefault(l["category"], {
            "listCount": 0, "orderCount": 0, "amount": 0.0,
            "levels": {"P0": 0, "P1": 0, "P2": 0, "P3": 0},
        })
        c["listCount"] += 1
    # 确保配置的所有板块都出现（即使暂无数据），方便前端下拉固定板块
    for name, _ in LIST_CATEGORY_RULES:
        category_map.setdefault(name, {
            "listCount": 0, "orderCount": 0, "amount": 0.0,
            "levels": {"P0": 0, "P1": 0, "P2": 0, "P3": 0},
        })
    categories = [
        {
            "name": name,
            "listCount": c["listCount"],
            "orderCount": c["orderCount"],
            "amount": round(c["amount"], 2),
            "amountText": money(c["amount"]),
            "levels": c["levels"],
        }
        for name, c in category_map.items()
    ]
    cat_order = {name: i for i, (name, _) in enumerate(LIST_CATEGORY_RULES)}
    categories.sort(key=lambda c: cat_order.get(c["name"], 99))

    return {
        "kpis": {
            "total": total,
            "today": today,
            "overdue": overdue,
            "amount": money(amount_sum),
            "suppliers": suppliers,
            "avgWaiting": avg_waiting,
            "lists": len(lists),
        },
        "orders": items,
        "lists": lists,
        "categories": categories,
        "suppliers": supplier_rows,
    }

def resolve_urgent_domain(client):
    fields = client.call_kw('purchase.order','fields_get',[],{'attributes':['type','selection']})
    for field in ['x_studio_urgent','x_urgent','urgent','x_studio_is_urgent','x_studio_priority','priority']:
        info = fields.get(field)
        if not info: continue
        if info['type'] == 'boolean': return [[field,'=',True]]
        if info['type'] == 'selection':
            keys = [str(k) for k,v in info.get('selection',[]) if '紧急' in str(v) or 'urgent' in str(v).lower()]
            if keys: return [[field,'in',keys]]
        elif info['type'] in ('char','text'):
            return ['|',[field,'ilike','紧急'],[field,'ilike','urgent']]
    raise OdooError('无法确认紧急字段映射；不得按单号猜测紧急订单')

def build(client):
    data = build_urgent_orders(client)
    currencies = {o['currency'] for o in data['orders']}
    if len(currencies) == 1:
        currency = next(iter(currencies))
        data['kpis']['amount'] = f"{currency} {sum(o['amount'] for o in data['orders']):,.2f}"
    elif currencies:
        data['kpis']['amount'] = '多币种 · 不合计'
    else:
        data['kpis']['amount'] = '—'
    # Amount-ranked summaries from the old screen incorrectly summed currencies.
    # The current renderer groups orders itself with currency-aware totals.
    data['suppliers'] = []
    data['lists'] = []
    for category in data['categories']:
        category['amountText'] = '按单据币种查看'
    return data
