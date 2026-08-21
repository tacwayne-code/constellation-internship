"""交付塔 API：销售订单聚合 + 紧急继承 sync"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from app.config import get_settings
from app.services.aggregation import (
    _ref_id,
    _ref_name,
    aggregate_order,
    get_sales_overview,
)
from app.services.odoo.client import OdooClient
from app.services.sync.emergency_propagation import propagate_emergency

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/delivery-tower", tags=["delivery-tower"])


def get_client() -> OdooClient:
    return OdooClient.get_instance(get_settings())


ClientDep = Annotated[OdooClient, Depends(get_client)]


@router.get("/sales")
async def sales_overview(
    client: ClientDep,
    resp: Response,
    limit: int = Query(100, ge=1, le=500),
):
    """销售订单总览：紧急判定 + PO/MO/Picking 子单计数"""
    if settings := get_settings():
        if settings.USE_MOCK:
            resp.headers["X-Data-Source"] = "mock"
            return []
    try:
        rows = await get_sales_overview(client, limit=limit)
    except Exception as e:  # noqa: BLE001
        logger.exception("sales_overview failed")
        raise HTTPException(status_code=500, detail=str(e))
    if rows:
        resp.headers["X-Data-Source"] = "odoo"
    return rows


@router.get("/delivery-overview")
async def delivery_overview(
    client: ClientDep,
    resp: Response,
    limit: int = Query(500, ge=1, le=1000),
):
    """跨订单交付风险总览：逾期 / 紧急 / 未完成聚合（轻量，复用 sales overview，不做 BOM 齐套）"""
    from datetime import date

    rows = await get_sales_overview(client, limit=limit)
    today = date.today()

    overdue: list[dict] = []
    urgent: list[dict] = []
    unfinished: list[dict] = []
    for s in rows:
        state = s.get("state")
        if state in ("done", "cancel"):
            continue
        unfinished.append(s)
        if s.get("is_emergency"):
            urgent.append(s)
        cd = s.get("commitment_date")
        if cd:
            try:
                d = date.fromisoformat(str(cd)[:10])
                od = (today - d).days
                if od > 0:
                    overdue.append({"id": s["id"], "name": s["name"], "partner": s["partner"],
                                    "state": state, "commitment_date": str(cd)[:10],
                                    "overdue_days": od, "is_emergency": s.get("is_emergency"),
                                    "po_count": s.get("po_count"), "mo_count": s.get("mo_count")})
            except ValueError:
                pass
    overdue.sort(key=lambda x: -x["overdue_days"])

    resp.headers["X-Data-Source"] = "odoo"
    return {
        "stats": {
            "total": len(rows),
            "overdue": len(overdue),
            "urgent": len(urgent),
            "unfinished": len(unfinished),
        },
        "overdue_orders": overdue[:20],
        "urgent_orders": [
            {"id": s["id"], "name": s["name"], "partner": s["partner"], "state": s["state"],
             "po_count": s.get("po_count"), "po_urgent": s.get("po_urgent"),
             "mo_count": s.get("mo_count"), "mo_urgent": s.get("mo_urgent")}
            for s in urgent[:20]
        ],
    }


@router.get("/logistics")
async def logistics(
    client: ClientDep,
    resp: Response,
    limit: int | None = Query(None, ge=1, le=10000, description="留空则拉全部"),
):
    """物流查看：销售出货(outgoing) + 采购收货(incoming) + 内部流转，按 flow 分组（全量）"""
    from app.services.odoo.models import FIELDS_PICKING

    picks = await client.search_read(
        "stock.picking", [], FIELDS_PICKING,
        limit=limit, order="scheduled_date desc, id desc",
    )
    if not picks:
        resp.headers["X-Data-Source"] = "empty"
        return {"stats": {"total": 0, "incoming": 0, "outgoing": 0, "internal": 0, "in_transit": 0}, "incoming": [], "outgoing": [], "internal": []}

    pt_ids = list({_ref_id(p.get("picking_type_id")) for p in picks if p.get("picking_type_id")})
    pt_map: dict[int, str] = {}
    if pt_ids:
        pt_recs = await client.search_read("stock.picking.type", [["id", "in", pt_ids]], ["id", "code"], limit=None)
        pt_map = {t["id"]: (t.get("code") or "internal") for t in pt_recs}

    def _eta_status(d: str | None) -> str:
        """预计到达状态：overdue(已逾期)/today(今日)/soon(3天内)/ok(正常)/none"""
        if not d:
            return "none"
        try:
            from datetime import date

            dd = date.fromisoformat(str(d)[:10])
            delta = (dd - date.today()).days
            if delta < 0:
                return "overdue"
            if delta == 0:
                return "today"
            if delta <= 3:
                return "soon"
            return "ok"
        except ValueError:
            return "none"

    def _item(p: dict) -> dict:
        eta = (p.get("scheduled_date") or "")[:10] or None
        state = p.get("state")
        # 已完成 = 已到达（不标逾期）；取消 = 不评估
        eta_status = "done" if state == "done" else ("cancel" if state == "cancel" else _eta_status(eta))
        return {
            "id": p["id"],
            "name": p.get("name"),
            "flow": pt_map.get(_ref_id(p.get("picking_type_id")), "internal"),
            "origin": p.get("origin"),
            "partner": _ref_name(p.get("partner_id")),
            "state": p.get("state"),
            "scheduled_date": p.get("scheduled_date"),
            "eta": eta,
            "eta_status": eta_status,
            "carrier": _ref_name(p.get("carrier_id")),
            "tracking_ref": p.get("carrier_tracking_ref"),
            "move_type": p.get("move_type"),
        }

    items = [_item(p) for p in picks]
    incoming = [i for i in items if i["flow"] == "incoming"]
    outgoing = [i for i in items if i["flow"] == "outgoing"]
    internal = [i for i in items if i["flow"] == "internal"]
    in_transit = [i for i in items if i["state"] in ("assigned", "confirmed", "waiting")]

    resp.headers["X-Data-Source"] = "odoo"
    return {
        "stats": {
            "total": len(items),
            "incoming": len(incoming),
            "outgoing": len(outgoing),
            "internal": len(internal),
            "in_transit": len(in_transit),
        },
        "showing": len(items),
        "incoming": incoming,
        "outgoing": outgoing,
        "internal": internal,
    }


@router.get("/orders/lookup")
async def order_lookup(
    client: ClientDep,
    name: str = Query(..., description="销售订单单号，如 S00124 / SO-2026-0042"),
):
    """按单号定位销售订单（模糊匹配 name 前缀），返回匹配列表"""
    rows = await client.search_read(
        "sale.order",
        [["name", "ilike", name]],
        ["id", "name", "state", "partner_id", "date_order", "amount_total"],
        limit=20, order="id desc",
    )
    return [
        {
            "id": r["id"],
            "name": r.get("name"),
            "state": r.get("state"),
            "partner": _ref_name(r.get("partner_id")),
            "date_order": r.get("date_order"),
            "amount_total": r.get("amount_total"),
        } for r in rows
    ]


@router.get("/orders/{so_id}/urgent-purchase-options")
async def order_urgent_purchase_options(
    so_id: int,
    client: ClientDep,
    resp: Response,
):
    """一键生成前置：需采购配件的供应商候选列表（供前端选择供应商，替代默认供应商）"""
    from app.services.urgent_purchase import get_urgent_purchase_options

    try:
        data = await get_urgent_purchase_options(client, so_id)
    except Exception as e:  # noqa: BLE001
        logger.exception("urgent_purchase_options(%s) failed", so_id)
        raise HTTPException(status_code=500, detail=str(e))
    if "error" in data:
        raise HTTPException(status_code=404, detail=data["error"])
    resp.headers["X-Data-Source"] = "odoo"
    return data


class UrgentPurchaseRequest(BaseModel):
    """创建紧急采购单请求：vendors = {product_id: partner_id}（可选，缺省用默认供应商）"""
    vendors: dict[int, int] | None = None


@router.post("/orders/{so_id}/create-urgent-purchases")
async def order_create_urgent_purchases(
    so_id: int,
    client: ClientDep,
    resp: Response,
    req: UrgentPurchaseRequest | None = None,
):
    """一键生成紧急采购订单：对需采购配件（无库存且无在途）创建 priority=1 的 RFQ

    请求体可选 {"vendors": {"<product_id>": <partner_id>}} 指定供应商；缺省用默认供应商。
    """
    from app.services.urgent_purchase import create_urgent_purchases

    try:
        data = await create_urgent_purchases(client, so_id,
                                             vendors=(req.vendors if req else None))
    except Exception as e:  # noqa: BLE001
        logger.exception("create_urgent_purchases(%s) failed", so_id)
        raise HTTPException(status_code=500, detail=str(e))
    if "error" in data:
        raise HTTPException(status_code=404, detail=data["error"])
    resp.headers["X-Data-Source"] = "odoo"
    return data


@router.get("/orders/{so_id}/delivery-analysis")
async def order_delivery_analysis(
    so_id: int,
    client: ClientDep,
    resp: Response,
):
    """交付日期分析：物料齐套 → 采购在途 → 预计到货 → 整单预计交付日 + 逾期风险"""
    from app.services.delivery_analysis import analyze_delivery

    try:
        data = await analyze_delivery(client, so_id)
    except Exception as e:  # noqa: BLE001
        logger.exception("order_delivery_analysis(%s) failed", so_id)
        raise HTTPException(status_code=500, detail=str(e))
    if "error" in data:
        raise HTTPException(status_code=404, detail=data["error"])
    resp.headers["X-Data-Source"] = "odoo"
    return data


@router.get("/orders/{so_id}")
async def order_detail(
    so_id: int,
    client: ClientDep,
    resp: Response,
):
    """单订单聚合：SO + PO + MO + Picking + BOM 树"""
    try:
        data = await aggregate_order(client, so_id)
    except Exception as e:  # noqa: BLE001
        logger.exception("order_detail(%s) failed", so_id)
        raise HTTPException(status_code=500, detail=str(e))
    if "error" in data:
        raise HTTPException(status_code=404, detail=data["error"])
    resp.headers["X-Data-Source"] = "odoo"
    return data


@router.post("/sync/emergency")
async def sync_emergency(
    client: ClientDep,
    resp: Response,
):
    """手动触发：扫描带紧急 tag 的销售订单，写回 PO/MO priority=3

    正常情况下由调度任务触发；前端"立即同步"按钮可调此接口。
    """
    try:
        result = await propagate_emergency(client)
    except Exception as e:  # noqa: BLE001
        logger.exception("sync_emergency failed")
        raise HTTPException(status_code=500, detail=str(e))
    resp.headers["X-Data-Source"] = "odoo"
    return result


# ====================================================================
#  生产加工工序进度
# ====================================================================

@router.get("/productions")
async def productions(
    client: ClientDep,
    resp: Response,
    limit: int = Query(200, ge=1, le=500),
    urgent_only: bool = Query(False),
):
    """生产工单（MO）列表 + 工序进度汇总（mrp.production + mrp.workorder 聚合）"""
    from app.services.odoo.models import FIELDS_MRP_PRODUCTION

    domain: list = [["state", "not in", ["cancel"]]]
    if urgent_only:
        domain.append(["priority", "=", "1"])

    mos = await client.search_read(
        "mrp.production", domain, FIELDS_MRP_PRODUCTION,
        limit=limit, order="id desc",
    )
    if not mos:
        resp.headers["X-Data-Source"] = "empty"
        return {"stats": {"total": 0, "progress": 0, "done": 0, "urgent": 0}, "items": []}

    mo_ids = [m["id"] for m in mos]
    wos = await client.search_read(
        "mrp.workorder",
        [["production_id", "in", mo_ids]],
        ["id", "production_id", "state", "workcenter_id", "operation_id",
         "date_start", "date_finished", "duration_expected", "duration", "qty_produced"],
        limit=20000,
    )
    wo_by_mo: dict[int, list[dict]] = {}
    for w in wos:
        pid = w.get("production_id")
        if isinstance(pid, (list, tuple)) and pid:
            wo_by_mo.setdefault(pid[0], []).append(w)

    items = []
    for m in mos:
        wo_list = wo_by_mo.get(m["id"], [])
        states = [w.get("state") for w in wo_list]
        items.append({
            "id": m["id"],
            "name": m.get("name"),
            "product": _ref_name(m.get("product_id")),
            "product_qty": m.get("product_qty"),
            "state": m.get("state"),
            "priority": m.get("priority") or "0",
            "is_urgent": (m.get("priority") or "") == "1",
            "date_start": m.get("date_start"),
            "date_finished": m.get("date_finished"),
            "bom_id": _ref_id(m.get("bom_id")),
            "bom_name": _ref_name(m.get("bom_id")),
            "workorder_count": len(wo_list),
            "workorder_done": sum(1 for s in states if s == "done"),
            "workorder_progress": sum(1 for s in states if s == "progress"),
            "workorder_states": sorted(set(states)),
        })

    stats = {
        "total": len(items),
        "progress": sum(1 for i in items if i["state"] == "progress"),
        "done": sum(1 for i in items if i["state"] == "done"),
        "urgent": sum(1 for i in items if i["is_urgent"]),
    }
    resp.headers["X-Data-Source"] = "odoo"
    return {"stats": stats, "items": items}


@router.get("/productions/{mo_id}/workorders")
async def production_workorders(
    mo_id: int,
    client: ClientDep,
    resp: Response,
    limit: int = Query(200, ge=1, le=500),
):
    """某生产工单的工序明细（mrp.workorder，含工位/耗时/进度）"""
    from app.services.odoo.models import FIELDS_WORKORDER

    try:
        wos = await client.search_read(
            "mrp.workorder", [["production_id", "=", mo_id]],
            FIELDS_WORKORDER, limit=limit, order="sequence, id",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("production_workorders(%s) 失败: %s", mo_id, e)
        resp.headers["X-Data-Source"] = "empty"
        return []

    items = []
    for w in wos:
        items.append({
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
        })
    resp.headers["X-Data-Source"] = "odoo" if items else "empty"
    return items