"""交付塔聚合服务：把单销售订单展开为「采购/生产/物流/BOM」四链路"""
from __future__ import annotations

import logging
from typing import Any

from app.services.cache import cached
from app.services.odoo.client import OdooClient
from app.services.odoo.models import (
    FIELDS_BOM,
    FIELDS_BOM_LINE,
    FIELDS_MRP_PRODUCTION,
    FIELDS_PICKING,
    FIELDS_PURCHASE,
    FIELDS_SALE_ORDER,
    FIELDS_WORKORDER,
    MODEL_BOM,
    MODEL_BOM_LINE,
    MODEL_CRM_TAG,
    MODEL_MRP_PRODUCTION,
    MODEL_PICKING,
    MODEL_PURCHASE,
    MODEL_PURCHASE_LINE,
    MODEL_SALE_ORDER,
    MODEL_SALE_ORDER_LINE,
    TAG_NAMES_EMERGENCY,
)
from app.services.odoo.refs import _ref_id, _ref_name

logger = logging.getLogger(__name__)


async def _get_emergency_tag_ids(client: OdooClient) -> set[int]:
    """返回紧急标签 id 集合（Odoo 18 XML-RPC 的 m2m 返回纯 id 列表，故用 id 交集判断）"""
    tags = await client.search_read(MODEL_CRM_TAG,
        [["name", "in", list(TAG_NAMES_EMERGENCY)]], ["id", "name"])
    return {t["id"] for t in tags}


def _tag_id_of(t) -> int | None:
    """m2m 元素可能是 int（Odoo 18 纯 id 列表）或 (id, name) 元组"""
    if isinstance(t, (list, tuple)):
        return t[0] if t else None
    if isinstance(t, int):
        return t
    return None


def _has_emergency_tag(tag_ids: list, emergency_ids: set[int]) -> bool:
    """检查 SO 的 tag_ids 是否含紧急 tag（id 交集，兼容 int / tuple 两种形态）"""
    if not tag_ids or not emergency_ids:
        return False
    return any(_tag_id_of(t) in emergency_ids for t in tag_ids)


# ---- 业务类型归类（按订单行产品分类） ----
_BUSINESS_WAREHOUSE_KW = ("堆垛", "机器人", "RGV", "立体", "仓储", "输送", "穿梭", "货架", "AGV", "分拣", "升降")
_BUSINESS_LED_KW = ("编带", "分光")


async def _get_so_business_types(client: OdooClient, so_ids: list[int]) -> dict[int, str]:
    """按销售订单行产品分类归类业务类型：warehouse(仓储设备) / led(分光编带) / other"""
    if not so_ids:
        return {}
    sols = await client.search_read(
        MODEL_SALE_ORDER_LINE, [["order_id", "in", so_ids]],
        ["id", "order_id", "product_id"], limit=None)
    prod_ids = {_ref_id(l.get("product_id")) for l in sols if l.get("product_id")}
    categ_map: dict[int, str] = {}
    if prod_ids:
        prods = await client.search_read(
            "product.product", [["id", "in", list(prod_ids)]],
            ["id", "categ_id"], limit=None)
        categ_map = {p["id"]: _ref_name(p.get("categ_id")) for p in prods}
    so_categ: dict[int, set[str]] = {}
    for l in sols:
        so_id = _ref_id(l.get("order_id"))
        if so_id is None:
            continue
        pid = _ref_id(l.get("product_id"))
        so_categ.setdefault(so_id, set()).add(categ_map.get(pid or 0, ""))
    result: dict[int, str] = {}
    for so_id, cs in so_categ.items():
        joined = " ".join(cs)
        if any(k in joined for k in _BUSINESS_WAREHOUSE_KW):
            result[so_id] = "warehouse"
        elif any(k in joined for k in _BUSINESS_LED_KW):
            result[so_id] = "led"
        else:
            result[so_id] = "other"
    return result


@cached(ttl=120, key_fn=lambda client, limit=100: f"sales_overview:{limit}")
async def get_sales_overview(client: OdooClient, limit: int = 100) -> list[dict[str, Any]]:
    """销售订单列表 + 紧急判定 + 关联子单聚合（PO/MO/picking 数量）"""
    emergency_ids = await _get_emergency_tag_ids(client)
    sos = await client.search_read(
        MODEL_SALE_ORDER,
        [["state", "not in", ["cancel", False]]],
        FIELDS_SALE_ORDER,
        limit=limit, order="date_order desc, id desc",
    )
    if not sos:
        return []

    so_names = [s["name"] for s in sos]
    so_ids = [s["id"] for s in sos]

    # ── MO 计数（origin = SO name） ──
    mos = await client.search_read(MODEL_MRP_PRODUCTION,
        [["origin", "in", so_names]],
        ["id", "origin", "state", "priority"], limit=10000)
    mo_count: dict[str, dict] = {}
    for m in mos:
        o = m.get("origin") or ""
        g = mo_count.setdefault(o, {"total": 0, "urgent": 0, "states": set()})
        g["total"] += 1
        if (m.get("priority") or "") == "1":
            g["urgent"] += 1
        g["states"].add(m.get("state"))

    # ── Picking 计数（origin = SO name） ──
    picks = await client.search_read(MODEL_PICKING,
        [["origin", "in", so_names]],
        ["id", "origin", "state"], limit=10000)
    pick_count: dict[str, dict] = {}
    for p in picks:
        o = p.get("origin") or ""
        g = pick_count.setdefault(o, {"total": 0, "states": set()})
        g["total"] += 1
        g["states"].add(p.get("state"))

    # ── PO 计数：sol → pol(sale_line_id) → po.order ──
    po_count: dict[int, dict] = {}  # key = so_id
    sol_ids = [
        l["id"] for l in await client.search_read(
            MODEL_SALE_ORDER_LINE, [["order_id", "in", so_ids]],
            ["id"], limit=None,
        )
    ]
    if sol_ids:
        sol_lines = await client.search_read(MODEL_SALE_ORDER_LINE,
            [["id", "in", sol_ids]], ["id", "order_id"], limit=None)
        sol_to_so: dict[int, int] = {sl["id"]: sl["order_id"][0] for sl in sol_lines if sl.get("order_id")}

        pols = await client.search_read(MODEL_PURCHASE_LINE,
            [["sale_line_id", "in", sol_ids]],
            ["id", "order_id", "sale_line_id"], limit=None)
        po_to_so: dict[int, int] = {}
        for pl in pols:
            po_id = _ref_id(pl.get("order_id"))
            sid = pl.get("sale_line_id")
            if po_id and isinstance(sid, (list, tuple)) and sid:
                so_id = sol_to_so.get(sid[0])
                if so_id:
                    po_to_so[po_id] = so_id
        if po_to_so:
            pos = await client.search_read(MODEL_PURCHASE,
                [["id", "in", list(po_to_so.keys())]],
                ["id", "state", "priority"], limit=None)
            for p in pos:
                so_id = po_to_so.get(p["id"])
                g = po_count.setdefault(so_id, {"total": 0, "urgent": 0, "states": set()})
                g["total"] += 1
                if (p.get("priority") or "") == "1":
                    g["urgent"] += 1
                g["states"].add(p.get("state"))

    so_biz = await _get_so_business_types(client, so_ids)
    return _assemble_sales(sos, emergency_ids, mo_count, pick_count, po_count, so_biz)


def _assemble_sales(sos, emergency_ids, mo_count, pick_count, po_count, so_biz) -> list[dict]:
    rows = []
    for s in sos:
        so_id = s["id"]
        name = s.get("name")
        tags = s.get("tag_ids") or []
        tag_names = [t[1] for t in tags if isinstance(t, (list, tuple)) and len(t) > 1]
        is_urgent = _has_emergency_tag(tags, emergency_ids)
        mc = mo_count.get(name, {})
        pc = pick_count.get(name, {})
        poc = po_count.get(so_id, {})
        rows.append({
            "id": so_id,
            "name": name,
            "business_type": so_biz.get(so_id, "other"),
            "partner": _ref_name(s.get("partner_id")),
            "state": s.get("state"),
            "date_order": s.get("date_order"),
            "commitment_date": s.get("commitment_date"),
            "amount_total": s.get("amount_total"),
            "tag_ids": tags,
            "tag_names": tag_names,
            "is_emergency": is_urgent,
            "po_count": poc.get("total", 0),
            "po_urgent": poc.get("urgent", 0),
            "po_states": sorted(poc.get("states", set())),
            "mo_count": mc.get("total", 0),
            "mo_urgent": mc.get("urgent", 0),
            "mo_states": sorted(mc.get("states", set())),
            "picking_count": pc.get("total", 0),
            "picking_states": sorted(pc.get("states", set())),
        })
    return rows


async def aggregate_order(client: OdooClient, so_id: int) -> dict[str, Any]:
    """单订单聚合：SO 详情 + PO + MO + Picking + BOM 树"""
    emergency_ids = await _get_emergency_tag_ids(client)

    sos = await client.search_read(MODEL_SALE_ORDER, [["id", "=", so_id]], FIELDS_SALE_ORDER)
    if not sos:
        return {"error": f"sale.order id={so_id} 不存在"}
    so = sos[0]
    so_name = so["name"]
    tags = so.get("tag_ids") or []
    tag_names = [t[1] for t in tags if isinstance(t, (list, tuple)) and len(t) > 1]
    is_urgent = _has_emergency_tag(tags, emergency_ids)

    # 1. 销售订单行（用于后续反查 PO）
    sol_ids = [
        l["id"] for l in await client.search_read(
            MODEL_SALE_ORDER_LINE, [["order_id", "=", so_id]],
            ["id"], limit=None,
        )
    ]

    # 2. PO：双链路关联 —— sale_line_id 反查 ∪ origin=SO 名（一键生成的紧急 RFQ 走 origin）
    pos = []
    po_ids: set[int] = set()

    # 链路 A：sale.order.line → purchase.order.line.sale_line_id
    if sol_ids:
        pols = await client.search_read(MODEL_PURCHASE_LINE,
            [["sale_line_id", "in", sol_ids]],
            ["id", "order_id"], limit=None)
        for pl in pols:
            pid = _ref_id(pl.get("order_id"))
            if pid:
                po_ids.add(pid)

    # 链路 B：purchase.order.origin = SO 名（紧急 RFQ 生成时写入的 origin）
    origin_pos = await client.search_read(MODEL_PURCHASE,
        [["origin", "=", so_name]],
        ["id", "name"], limit=None)
    for op in origin_pos:
        po_ids.add(op["id"])

    pols_by_po: dict[int, list[dict]] = {}
    if po_ids:
        # 统一拉取所有候选 PO 的 lines（覆盖双链路，确保链路 B 的 PO 也能渲染采购物品）
        pols_all = await client.search_read(MODEL_PURCHASE_LINE,
            [["order_id", "in", list(po_ids)]],
            ["id", "order_id", "product_id", "product_qty", "qty_received", "state", "date_planned"],
            limit=None)
        for pl in pols_all:
            pid = _ref_id(pl.get("order_id"))
            if pid:
                pols_by_po.setdefault(pid, []).append(pl)

        po_recs = await client.search_read(MODEL_PURCHASE,
            [["id", "in", list(po_ids)]],
            FIELDS_PURCHASE + ["origin"], limit=None)
        pos = [
            {
                "id": p["id"], "name": p.get("name"),
                "partner": _ref_name(p.get("partner_id")),
                "state": p.get("state"),
                "priority": p.get("priority") or 0,
                "is_urgent": (p.get("priority") or "") == "1",
                "date_planned": p.get("date_planned"),
                "amount_total": p.get("amount_total"),
                "origin": p.get("origin"),
                "link": "sale_line_id" if p.get("origin") != so_name else "origin",
                "lines": [
                    {
                        "id": pl["id"],
                        "product": _ref_name(pl.get("product_id")),
                        "qty": pl.get("product_qty"),
                        "received": pl.get("qty_received"),
                        "state": pl.get("state"),
                        "date_planned": pl.get("date_planned"),
                    }
                    for pl in pols_by_po.get(p["id"], [])
                ],
            } for p in po_recs
        ]

    # 3. MO：通过 origin 匹配 + 批量拉取工序进度（消除 N+1）
    mos = await client.search_read(MODEL_MRP_PRODUCTION, [["origin", "=", so_name]],
        FIELDS_MRP_PRODUCTION, limit=None)
    mo_ids = [m["id"] for m in mos]
    wo_by_mo: dict[int, list[dict]] = {}
    if mo_ids:
        wos_all = await client.search_read(
            "mrp.workorder", [["production_id", "in", mo_ids]],
            FIELDS_WORKORDER, limit=None, order="sequence, id",
        )
        for w in wos_all:
            _pid = _ref_id(w.get("production_id"))
            if _pid is not None:
                wo_by_mo.setdefault(_pid, []).append(w)
    mo_list = []
    for m in mos:
        wos = wo_by_mo.get(m["id"], [])
        workorders = [
            {
                "id": w["id"],
                "name": w.get("name"),
                "operation": _ref_name(w.get("operation_id")),
                "workcenter": _ref_name(w.get("workcenter_id")),
                "state": w.get("state"),
                "date_start": w.get("date_start"),
                "date_finished": w.get("date_finished"),
                "duration_expected": w.get("duration_expected"),
                "duration": w.get("duration"),
                "qty_produced": w.get("qty_produced"),
            } for w in wos
        ]
        mo_list.append({
            "id": m["id"], "name": m.get("name"),
            "product": _ref_name(m.get("product_id")),
            "state": m.get("state"),
            "priority": m.get("priority") or 0,
            "is_urgent": (m.get("priority") or "") == "1",
            "product_qty": m.get("product_qty"),
            "date_start": m.get("date_start"),
            "date_finished": m.get("date_finished"),
            "bom_id": _ref_id(m.get("bom_id")),
            "bom_name": _ref_name(m.get("bom_id")),
            "workorders": workorders,
        })

    # 4. Picking：origin=SO 名（出货/内部）∪ origin=PO 名（采购收货物流），按流转类型分类
    picks = await client.search_read(MODEL_PICKING, [["origin", "=", so_name]],
        FIELDS_PICKING, limit=None)
    po_names = [p["name"] for p in pos if p.get("name")]
    if po_names:
        po_picks = await client.search_read(MODEL_PICKING,
            [["origin", "in", po_names]],
            FIELDS_PICKING, limit=None)
        seen_ids = {p["id"] for p in picks}
        picks = picks + [p for p in po_picks if p["id"] not in seen_ids]

    # picking_type → code（incoming=补货入库 / outgoing=出货 / internal=内部流转）
    pt_ids = list({_ref_id(p.get("picking_type_id")) for p in picks if p.get("picking_type_id")})
    pt_map: dict[int, dict] = {}
    if pt_ids:
        pt_recs = await client.search_read("stock.picking.type", [["id", "in", pt_ids]],
            ["id", "code", "name"], limit=None)
        pt_map = {t["id"]: t for t in pt_recs}

    pick_list = [
        {
            "id": p["id"], "name": p.get("name"),
            "state": p.get("state"),
            "picking_type_id": _ref_id(p.get("picking_type_id")),
            "flow": (pt_map.get(_ref_id(p.get("picking_type_id")), {}).get("code") or "internal"),
            "picking_type": _ref_name(p.get("picking_type_id")),
            "scheduled_date": p.get("scheduled_date"),
            "carrier": _ref_name(p.get("carrier_id")),
            "tracking_ref": p.get("carrier_tracking_ref"),
            "partner": _ref_name(p.get("partner_id")),
            "origin": p.get("origin"),
        } for p in picks
    ]


    # 5. BOM 树：把所有 MO 关联的 bom_id 合并去重，取 bom_line_ids
    bom_ids = list({_ref_id(m.get("bom_id")) for m in mos if m.get("bom_id")})
    bom_list = []
    if bom_ids:
        boms = await client.search_read(MODEL_BOM, [["id", "in", bom_ids]], FIELDS_BOM, limit=None)
        all_line_ids = []
        bom_to_lines: dict[int, list[int]] = {}
        for b in boms:
            line_ids = b.get("bom_line_ids") or []
            bom_to_lines[b["id"]] = line_ids
            all_line_ids.extend(line_ids)
        all_lines = []
        if all_line_ids:
            all_lines = await client.search_read(MODEL_BOM_LINE,
                [["id", "in", all_line_ids]], FIELDS_BOM_LINE, limit=None)
        line_map = {l["id"]: l for l in all_lines}
        for b in boms:
            bom_list.append({
                "id": b["id"],
                "display_name": b.get("display_name"),
                "product_tmpl": _ref_name(b.get("product_tmpl_id")),
                "product": _ref_name(b.get("product_id")),
                "code": b.get("code"),
                "type": b.get("type"),
                "lines": [
                    {
                        "id": lid,
                        "product": _ref_name(line_map[lid].get("product_id")),
                        "qty": line_map[lid].get("product_qty"),
                        "uom": _ref_name(line_map[lid].get("product_uom_id")),
                        "sequence": line_map[lid].get("sequence"),
                    } for lid in bom_to_lines[b["id"]] if lid in line_map
                ],
            })

    # 4.5 库存环节：订单产品 + BOM 子件的现存量/预报量/缺口
    sol_products = [
        (l.get("product_id")[0], _ref_name(l.get("product_id")))
        for l in await client.search_read(MODEL_SALE_ORDER_LINE,
            [["order_id", "=", so_id]], ["id", "product_id"], limit=None)
        if l.get("product_id")
    ]
    inv_products: dict[int, dict] = {}
    for pid, pname in sol_products:
        inv_products.setdefault(pid, {"id": pid, "name": pname, "role": "订单产品"})
    if bom_ids:
        bom_lines_all = await client.search_read(MODEL_BOM_LINE,
            [["bom_id", "in", bom_ids]], ["id", "product_id", "product_qty"], limit=None)
        for bl in bom_lines_all:
            if isinstance(bl.get("product_id"), (list, tuple)) and bl["product_id"]:
                pid = bl["product_id"][0]
                if pid not in inv_products:
                    inv_products[pid] = {"id": pid, "name": _ref_name(bl["product_id"]), "role": "BOM 配件"}
    inv_ids = list(inv_products.keys())
    inventory = []
    if inv_ids:
        stocks = await client.search_read("product.product", [["id", "in", inv_ids]],
            ["id", "name", "default_code", "qty_available", "virtual_available", "free_qty"],
            limit=None)
        stock_map = {s["id"]: s for s in stocks}
        quants = await client.search_read("stock.quant",
            [["product_id", "in", inv_ids], ["quantity", "!=", 0]],
            ["product_id", "location_id", "quantity", "reserved_quantity"], limit=2000)
        quant_map: dict[int, list[dict]] = {}
        for q in quants:
            qp = _ref_id(q.get("product_id"))
            if qp:
                quant_map.setdefault(qp, []).append({
                    "location": _ref_name(q.get("location_id")),
                    "quantity": q.get("quantity"),
                    "reserved": q.get("reserved_quantity"),
                })
        for pid, meta in inv_products.items():
            s = stock_map.get(pid, {})
            qty_available = s.get("qty_available") or 0
            virtual_available = s.get("virtual_available") or 0
            inventory.append({
                "id": pid,
                "name": meta["name"],
                "role": meta["role"],
                "qty_available": qty_available,
                "virtual_available": virtual_available,
                "free_qty": s.get("free_qty") or 0,
                "shortage": virtual_available < 0,  # 预报量<0 = 缺料风险
                "locations": quant_map.get(pid, []),
            })
    stock_shortage = sum(1 for i in inventory if i["shortage"])

    # 4.6 业务链路摘要：销售 → 采购/补货 → 库存 → 生产 → 物流
    po_states = [p["state"] for p in pos]
    mo_states = [m["state"] for m in mo_list]
    chain = {
        "sale": {"state": so.get("state"), "count": 1},
        "purchase": {
            "count": len(pos),
            "generated": len(pos) > 0,
            "urgent": sum(1 for p in pos if p["is_urgent"]),
            "states": sorted(set(po_states)),
        },
        "stock": {
            "products": len(inventory),
            "shortage": stock_shortage,
        },
        "production": {
            "count": len(mo_list),
            "urgent": sum(1 for m in mo_list if m["is_urgent"]),
            "states": sorted(set(mo_states)),
        },
        "logistics": {
            "count": len(pick_list),
            "incoming": sum(1 for p in pick_list if p["flow"] == "incoming"),
            "outgoing": sum(1 for p in pick_list if p["flow"] == "outgoing"),
        },
    }

    return {
        "sale_order": {
            "id": so["id"], "name": so_name,
            "partner": _ref_name(so.get("partner_id")),
            "state": so.get("state"),
            "date_order": so.get("date_order"),
            "commitment_date": so.get("commitment_date"),
            "amount_total": so.get("amount_total"),
            "tag_ids": tags,
            "tag_names": tag_names,
            "is_emergency": is_urgent,
        },
        "purchase_orders": pos,
        "productions": mo_list,
        "pickings": pick_list,
        "boms": bom_list,
        "inventory": inventory,
        "chain": chain,
        "summary": {
            "po_count": len(pos),
            "po_urgent": sum(1 for p in pos if p["is_urgent"]),
            "mo_count": len(mo_list),
            "mo_urgent": sum(1 for m in mo_list if m["is_urgent"]),
            "picking_count": len(pick_list),
            "bom_count": len(bom_list),
            "is_emergency": is_urgent,
        },
    }