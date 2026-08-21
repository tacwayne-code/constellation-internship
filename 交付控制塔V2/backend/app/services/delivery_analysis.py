"""交付日期分析：物料齐套 → 采购在途 → 预计到货 → 整单预计交付日

流程（不改 Odoo，纯 XML-RPC 标准模型）：
  1. 销售行产品 + BOM 配件需求展开
  2. 现存量(qty_available) vs 采购在途(未收完的 PO 行) → 缺口
  3. 每件预计到货日 ETA = 在途 PO 的 date_planned；无采购 → need_purchase
  4. 整单预计交付日 = max(所有 ETA, 生产完成日)，与承诺交期比逾期
"""
from __future__ import annotations

from typing import Any

from app.services.odoo.client import OdooClient
from app.services.odoo.models import (
    FIELDS_SALE_ORDER,
    MODEL_BOM,
    MODEL_BOM_LINE,
    MODEL_MRP_PRODUCTION,
    MODEL_PURCHASE,
    MODEL_PURCHASE_LINE,
    MODEL_SALE_ORDER,
    MODEL_SALE_ORDER_LINE,
)


def _ref_id(ref) -> int | None:
    if isinstance(ref, (list, tuple)):
        return ref[0] if ref else None
    if isinstance(ref, int):
        return ref
    return None


def _ref_name(ref) -> str:
    if isinstance(ref, (list, tuple)) and len(ref) > 1:
        return str(ref[1])
    return str(ref) if ref else "—"


# ---- 物流分类：标准件 vs 加工周期件（按产品分类关键词） ----
_STANDARD_KW = ("标准件", "螺丝", "螺母", "垫圈", "卡簧", "销钉", "轴承", "平垫", "弹垫", "顶丝", "接头", "端子", "紧固", "弹簧")
_MACHINED_KW = ("加工", "非标", "定制", "CNC", "钣金", "成套", "焊接", "机加", "铸", "锻")
_TRANSIT_DELAY_DAYS = 2  # 标准件「已发出」后允许的物流延迟（天）


def _material_type(categ_name: str) -> str:
    """物料类型：standard(标准件) / machined(加工周期件) / other(其他)"""
    if any(k in categ_name for k in _STANDARD_KW):
        return "standard"
    if any(k in categ_name for k in _MACHINED_KW):
        return "machined"
    return "other"


async def analyze_delivery(client: OdooClient, so_id: int) -> dict[str, Any]:
    """按销售订单做交付日期估算。返回含 materials / eta_summary / estimated_delivery。"""
    sos = await client.search_read(MODEL_SALE_ORDER, [["id", "=", so_id]], FIELDS_SALE_ORDER)
    if not sos:
        return {"error": f"sale.order id={so_id} 不存在"}
    so = sos[0]
    so_name = so["name"]
    commitment_date = so.get("commitment_date")

    # ── 1. 销售行需求 ──
    sols = await client.search_read(
        MODEL_SALE_ORDER_LINE, [["order_id", "=", so_id]],
        ["id", "product_id", "product_uom_qty"], limit=None,
    )
    demands: dict[int, float] = {}
    roles: dict[int, str] = {}
    sol_product_ids: list[int] = []
    for l in sols:
        if isinstance(l.get("product_id"), (list, tuple)) and l["product_id"]:
            pid = l["product_id"][0]
            demands[pid] = demands.get(pid, 0) + (l.get("product_uom_qty") or 0)
            roles[pid] = "订单产品"
            sol_product_ids.append(pid)

    # ── 2. BOM 配件需求展开（订单产品 → BOM → 子件） ──
    if sol_product_ids:
        products = await client.search_read(
            "product.product", [["id", "in", sol_product_ids]],
            ["id", "product_tmpl_id"], limit=None,
        )
        tmpl_ids = list({p["product_tmpl_id"][0] for p in products if p.get("product_tmpl_id")})
        if tmpl_ids:
            boms = await client.search_read(
                MODEL_BOM, [["product_tmpl_id", "in", tmpl_ids]],
                ["id", "product_tmpl_id", "product_qty", "bom_line_ids"], limit=None,
            )
            bom_qty_by_tmpl = {b["product_tmpl_id"][0]: (b.get("product_qty") or 1) for b in boms}
            main_demand_by_tmpl: dict[int, float] = {}
            for p in products:
                tmpl = p["product_tmpl_id"][0] if p.get("product_tmpl_id") else None
                if tmpl:
                    main_demand_by_tmpl[tmpl] = main_demand_by_tmpl.get(tmpl, 0) + demands.get(p["id"], 0)
            line_ids = [lid for b in boms for lid in (b["bom_line_ids"] or [])]
            if line_ids:
                bom_lines = await client.search_read(
                    MODEL_BOM_LINE, [["id", "in", line_ids]],
                    ["id", "bom_id", "product_id", "product_qty"], limit=None,
                )
                for bl in bom_lines:
                    if not (isinstance(bl.get("product_id"), (list, tuple)) and bl["product_id"]):
                        continue
                    comp_pid = bl["product_id"][0]
                    for b in boms:
                        if bl["bom_id"][0] == b["id"]:
                            tmpl = b["product_tmpl_id"][0]
                            bqty = bom_qty_by_tmpl.get(tmpl, 1) or 1
                            add = main_demand_by_tmpl.get(tmpl, 0) * ((bl.get("product_qty") or 0) / bqty)
                            if add > 0:
                                demands[comp_pid] = demands.get(comp_pid, 0) + add
                                roles[comp_pid] = "BOM 配件"
                            break

    if not demands:
        return {"error": "订单无销售行"}

    # ── 3. 现存量 ──
    pids = list(demands.keys())
    stocks = await client.search_read(
        "product.product", [["id", "in", pids]],
        ["id", "name", "default_code", "qty_available", "product_tmpl_id", "categ_id"], limit=None,
    )
    stock_map = {s["id"]: s for s in stocks}

    # ── 4. 采购在途（不限状态拉全部行：draft/sent 询价单也算"已存在 PO"，防止重复生成；cancel 排除） ──
    pols = await client.search_read(
        MODEL_PURCHASE_LINE,
        [["product_id", "in", pids], ["state", "!=", "cancel"]],
        ["id", "order_id", "product_id", "product_qty", "qty_received", "state", "date_planned"],
        limit=None,
    )
    po_ids = list({_ref_id(p.get("order_id")) for p in pols if p.get("order_id")})
    po_map: dict[int, str] = {}
    po_details: dict[int, dict[str, Any]] = {}
    if po_ids:
        pos = await client.search_read(MODEL_PURCHASE, [["id", "in", po_ids]],
            ["id", "name", "partner_id", "state", "priority", "date_planned"], limit=None)
        po_map = {p["id"]: p.get("name") for p in pos}
        po_details = {
            p["id"]: {
                "name": p.get("name"),
                "partner": _ref_name(p.get("partner_id")),
                "state": p.get("state"),
                "priority": p.get("priority") or "0",
                "is_urgent": (p.get("priority") or "") == "1",
                "date_planned": p.get("date_planned"),
            } for p in pos
        }

    # ── 4.5 采购收货物流：按 PO origin 拉 picking（单号/状态/计划到货/完成时间） ──
    pick_map: dict[str, list[dict]] = {}
    if po_ids:
        po_names = [po_map[i] for i in po_ids if i in po_map]
        picks = await client.search_read(
            "stock.picking",
            [["origin", "in", po_names], ["picking_type_id", "!=", False]],
            ["id", "name", "origin", "state", "scheduled_date", "carrier_tracking_ref", "date_done"],
            limit=None,
        )
        for pk in picks:
            pick_map.setdefault(pk.get("origin") or "", []).append(pk)

    # ── 5. 逐件计算（只在途 = 未收完的 PO 行） ──
    materials = []
    for pid, demand in sorted(demands.items(), key=lambda x: -x[1]):
        s = stock_map.get(pid, {})
        available = s.get("qty_available") or 0
        all_lines = [l for l in pols if _ref_id(l.get("product_id")) == pid]
        # 有效在途：已下单(purchase/done)且未收完的行
        open_lines = [
            l for l in all_lines
            if (l.get("state") or "draft") in ("purchase", "done")
            and (l.get("product_qty") or 0) - (l.get("qty_received") or 0) > 0
        ]
        in_transit = sum((l.get("product_qty") or 0) - (l.get("qty_received") or 0) for l in open_lines)
        # 关联采购收货物流（按 PO origin）
        rel_picks: list[dict] = []
        for l in open_lines:
            o = po_map.get(_ref_id(l.get("order_id")))
            if o:
                rel_picks.extend(pick_map.get(o, []))
        # 物料类型（标准件/加工周期件）+ 物流单号
        categ_name = _ref_name(s.get("categ_id"))
        mtype = _material_type(categ_name)
        tracking_refs = sorted({pk.get("carrier_tracking_ref") for pk in rel_picks if pk.get("carrier_tracking_ref")})
        # ── 动态 ETA：标准件按物流状态推算，加工周期件用预计交付时间 ──
        etas = [l.get("date_planned") for l in open_lines if l.get("date_planned")]
        base_eta = max(etas)[:10] if etas else None
        eta = base_eta
        logistics_note = ""
        if mtype == "standard" and rel_picks:
            done_picks = [pk for pk in rel_picks if pk.get("state") == "done"]
            shipped_picks = [pk for pk in rel_picks if pk.get("state") in ("assigned", "confirmed")]
            if done_picks:
                done_dates = [(pk.get("date_done") or "")[:10] for pk in done_picks if pk.get("date_done")]
                if done_dates:
                    eta = max(done_dates)
                    logistics_note = "已到货"
            elif shipped_picks:
                from datetime import date as _date, timedelta as _td

                eta = (_date.today() + _td(days=_TRANSIT_DELAY_DAYS)).isoformat()
                logistics_note = f"已发出·+{_TRANSIT_DELAY_DAYS}天"
        on_order = sorted({
            po_map[_ref_id(l.get("order_id"))] for l in open_lines
            if _ref_id(l.get("order_id")) in po_map
        })
        gap = round(demand - available - in_transit, 3)
        # 已存在的关联 PO（不限状态：draft 询价单也算），用于避免重复生成
        existing_po_names = sorted({
            po_map[_ref_id(l.get("order_id"))]
            for l in all_lines
            if _ref_id(l.get("order_id")) in po_map
        })
        # 关联 PO 详情（按 PO 去重），供前端悬浮展示供应商/状态/交期/数量
        existing_po_details_map: dict[int, dict[str, Any]] = {}
        for l in all_lines:
            pid_po = _ref_id(l.get("order_id"))
            if pid_po in po_details and pid_po not in existing_po_details_map:
                existing_po_details_map[pid_po] = {
                    **po_details[pid_po],
                    "qty_ordered": l.get("product_qty"),
                    "qty_received": l.get("qty_received"),
                }
        existing_po_details = sorted(
            existing_po_details_map.values(),
            key=lambda x: (x["date_planned"] or "", x["name"]),
        )
        has_existing_po = bool(existing_po_names)
        # 严格需采购：缺口>0 且 无在途 且 无任何已存在 PO（真正能新增的）
        need_purchase = gap > 0 and not open_lines and not has_existing_po
        # 配件状态（含物流同步 + 已询价）：充足 / 在途采购 / 已询价 / 需采购
        if gap <= 0:
            status, status_tone = "充足", "ok"
        elif open_lines:
            status, status_tone = "在途采购", "transit"
        elif has_existing_po:
            status, status_tone = "已询价", "quoted"
        else:
            status, status_tone = "需采购", "need"
        materials.append({
            "product_id": pid,
            "product_tmpl_id": _ref_id(s.get("product_tmpl_id")),
            "product": s.get("name") or f"P{pid}",
            "role": roles.get(pid, ""),
            "material_type": mtype,
            "logistics_note": logistics_note,
            "tracking_refs": tracking_refs,
            "demand": demand,
            "available": available,
            "in_transit": in_transit,
            "gap": gap,
            "need_purchase": need_purchase,
            "has_existing_po": has_existing_po,
            "existing_po_names": existing_po_names,
            "existing_po_details": existing_po_details,
            "status": status,
            "status_tone": status_tone,
            "eta": eta,
            "eta_source": "在途采购" if eta else ("已询价" if has_existing_po else ("需采购" if need_purchase else "—")),
            "on_order": on_order,
        })

    # ── 5.5 物流同步：配件相关采购收货物流（incoming picking）状态回写配件 ──
    # ── 5.5 物流同步：配件状态回写（pick_map 已在上方按 PO origin 拉取） ──
    for m in materials:
        m["pickings"] = [
            {
                "name": pk.get("name"),
                "state": pk.get("state"),
                "scheduled_date": (pk.get("scheduled_date") or "")[:10],
                "carrier": _ref_name(pk.get("carrier_id")),
                "tracking_ref": pk.get("carrier_tracking_ref"),
            }
            for o in m.get("on_order", [])
            for pk in pick_map.get(o, [])
        ]
        # 在途采购 + 全部收货物流已完成 → 已到货（物流驱动配件状态）
        if m["status"] == "在途采购" and m["pickings"] and all(pk["state"] == "done" for pk in m["pickings"]):
            m["status"], m["status_tone"] = "已到货", "done"
            m["eta_source"] = "已到货"

    # ── 6. 整单预计交付日 ──
    eta_values = [m["eta"] for m in materials if m["eta"] and m["status"] == "在途采购"]
    need_purchase_list = [m for m in materials if m["need_purchase"]]
    gap_list = [m for m in materials if m["gap"] > 0]

    mos = await client.search_read(
        MODEL_MRP_PRODUCTION, [["origin", "=", so_name]], ["id", "date_finished"], limit=None,
    )
    mo_finish = max((m.get("date_finished") or "")[:10] for m in mos if m.get("date_finished")) if mos else None

    if need_purchase_list:
        estimated_date = None
        source = "存在待采购件，交付日待定"
        risk = "high"
    elif eta_values:
        estimated_date = max(eta_values)
        source = "最晚到货日"
        risk = "high" if gap_list else "mid"
    elif mo_finish:
        estimated_date = mo_finish
        source = "生产完成日"
        risk = "ok"
    else:
        estimated_date = commitment_date[:10] if commitment_date else None
        source = "订单承诺交期"
        risk = "ok"

    overdue_days = 0
    if estimated_date and commitment_date:
        try:
            from datetime import date

            d_est = date.fromisoformat(estimated_date)
            d_commit = date.fromisoformat(str(commitment_date)[:10])
            overdue_days = (d_est - d_commit).days
            if overdue_days > 0 and risk == "ok":
                risk = "mid"
        except ValueError:
            pass

    return {
        "so": {"name": so_name, "state": so.get("state"), "commitment_date": commitment_date},
        "materials": materials,
        "eta_summary": {
            "total": len(materials),
            "gap_count": len(gap_list),
            "need_purchase": len(need_purchase_list),
            "need_purchase_new": sum(1 for m in materials if m["need_purchase"]),
            "quoted": sum(1 for m in materials if m["has_existing_po"]),
            "in_transit_count": sum(1 for m in materials if m["in_transit"] > 0),
        },
        "estimated_delivery": {
            "date": estimated_date,
            "source": source,
            "commitment_date": commitment_date[:10] if commitment_date else None,
            "overdue_days": overdue_days,
            "risk": risk,
        },
    }
