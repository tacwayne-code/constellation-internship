"""Extracted display calculations from ERP看板; local overrides and mutation handlers removed."""
import json, statistics, time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from lighthouse.odoo import OdooError
DASHBOARD_MAX_ORDERS = 1200
DASHBOARD_MAX_MOVES = 8000
STATE_LABELS = {'draft':'询价','sent':'已发送','to approve':'待审批','purchase':'采购订单','done':'已完成','cancel':'已取消','assigned':'可用','waiting':'等待中','confirmed':'等待中'}
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
    auto_completed_count = 0  # Odoo 原始补货记录；不在灯塔确认或自动隐藏
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

def split_product_display(value):
    text = str(value or "").strip()
    if text.startswith("[") and "]" in text:
        code, name = text[1:].split("]", 1)
        return code.strip(), name.strip() or text
    return "", text

def build(client):
    warehouse = build_warehouse(client)
    purchase = build_purchase(client)
    quants = warehouse.pop('_internalQuants', [])
    zero = warehouse.pop('_zeroQuants', [])
    lines = build_purchase_lines(client)
    points = build_orderpoints(client)
    replenishment = build_replenishment_list(client)
    consumption = build_consumption_rates(client)
    snapshot = build_stock_snapshot(quants, points)
    urgency = build_product_urgency(snapshot, points, consumption, lines)
    orders = client.search_read_all('purchase.order', ['partner_id'], [], max_rows=DASHBOARD_MAX_ORDERS)
    return {**warehouse, **purchase, 'procurementGap':build_procurement_gap(zero, lines),
        'purchaseLines':lines, 'supplierContacts':build_supplier_contacts(client, orders),
        'productSuppliers':build_product_suppliers(client, [x.get('product','') for x in replenishment['items']]),
        'orderpoints':points, 'replenishmentList':replenishment, 'consumption':consumption,
        'productUrgency':urgency, 'manualUrgentItems':[], 'tracking':{'requests':{},'confirmed':{}}, 'meta':{'source':'odoo'}}
