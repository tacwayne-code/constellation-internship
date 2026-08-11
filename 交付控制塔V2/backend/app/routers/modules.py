"""通用模块路由：交付包/采购/物流/库存/班组/供应商 + 模块配置"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from typing import Annotated

import logging

logger = logging.getLogger(__name__)

from app.config import get_settings
from app.services.adapters.base import fetch_mock_rows, fetch_with_fallback
from app.services.adapters.business import (
    BomAdapter,
    CommissioningAdapter,
    ElectricalAdapter,
    InventoryAdapter,
    LogisticsAdapter,
    PeopleAdapter,
    ProcurementAdapter,
    ProductAdapter,
    SaleOrderAdapter,
    VendorsAdapter,
    WorkcenterAdapter,
    WorkOrderAdapter,
)
from app.services.adapters.project import GanttAdapter
from app.services.odoo.client import OdooClient

router = APIRouter(prefix="/api", tags=["modules"])

_MODULES = {
    "overview": {"title": "项目总览", "adapter": None, "mock": None},
    "delivery": {"title": "交付包", "adapter": GanttAdapter(), "mock": "delivery_packages"},
    "design": {"title": "设计与图纸", "adapter": BomAdapter(), "mock": "bom"},
    "procurement": {"title": "采购与交期", "adapter": ProcurementAdapter(), "mock": "procurement"},
    "logistics": {"title": "物流管理", "adapter": LogisticsAdapter(), "mock": "logistics"},
    "inventory": {"title": "现场库存", "adapter": InventoryAdapter(), "mock": "inventory"},
    "people": {"title": "人员管理", "adapter": PeopleAdapter(), "mock": "people"},
    "vendors": {"title": "供应商交付", "adapter": VendorsAdapter(), "mock": "vendors"},
    "electrical": {"title": "电气施工", "adapter": ElectricalAdapter(), "mock": None},
    "field": {"title": "风险控制", "adapter": GanttAdapter(), "mock": "risks"},
    "mes": {"title": "MES / WCS 实施", "adapter": GanttAdapter(), "mock": None},
    "commissioning": {"title": "调试与验收", "adapter": None, "mock": None},
    # ---- B 组：新增业务模块 ----
    "sales": {"title": "销售订单", "adapter": SaleOrderAdapter(), "mock": "sales"},
    "products": {"title": "产品主数据", "adapter": ProductAdapter(), "mock": "products"},
    "manufacturing": {"title": "制造执行", "adapter": WorkOrderAdapter(), "mock": "manufacturing"},
    "workshop": {"title": "生产车间", "adapter": WorkcenterAdapter(), "mock": "workshop"},
}

# 各模块适配器的 Odoo domain（读取哪些记录）
_ADAPTER_DOMAIN = {
    "design": [("active", "=", True)],
    "procurement": [],
    "logistics": [],
    "inventory": [],
    "people": [],
    "vendors": [("supplier_rank", ">", 0)],
    "electrical": [("state", "not in", ["cancel"])],
    "field": [],  # 风险控制：显示全部 task（含已完成 + 进行中 + 未开始）
    "commissioning": [],
    # MES/软件实施：任务名含软件相关关键词
    "mes": ["|", "|", "|",
            ("name", "ilike", "MES"), ("name", "ilike", "WMS"),
            ("name", "ilike", "软件"), ("name", "ilike", "系统")],
    # ---- B 组：新模块 domain ----
    "sales": [("state", "not in", ["cancel"])],
    "products": [("active", "=", True)],
    "manufacturing": [("state", "not in", ["cancel"])],
}


def get_client() -> OdooClient:
    return OdooClient.get_instance(get_settings())


ClientDep = Annotated[OdooClient, Depends(get_client)]


@router.get("/modules/{module}/rows")
async def module_rows(
    module: str,
    client: ClientDep,
    resp: Response,
    project_id: str | None = None,
    limit: int = Query(100, ge=1, le=500),
):
    cfg = _MODULES.get(module)
    if cfg is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=f"未知模块: {module}")

    # 项目总览板块：返回项目组合摘要（含进度/状态增强）
    if module == "overview":
        from app.services.adapters.project import ProjectAdapter, enrich_projects

        projects, source = await fetch_with_fallback(
            client, ProjectAdapter(), mock_key="projects"
        )
        if source == "odoo" and projects:
            projects = await enrich_projects(client, projects)
        if not projects:
            projects = await fetch_mock_rows("projects")
            source = "mock"
        if source == "mock":
            resp.headers["X-Data-Source"] = "mock"
        return projects[:limit]

    # 现场库存板块：按产品聚合（同一产品多库位合并为一行）
    if module == "inventory":
        rows = await fetch_inventory_grouped(client, limit=limit)
        if rows:
            return rows
        resp.headers["X-Data-Source"] = "mock"
        return []

    # 调试与验收板块：mrp.production 已完成/待关闭工单作为验收清单
    if module == "commissioning":
        rows = await fetch_commissioning_items(client, limit=limit)
        if rows:
            return rows
        resp.headers["X-Data-Source"] = "mock"
        return []

    # 生产车间板块：mrp.workcenter + 在制工单聚合
    if module == "workshop":
        rows = await fetch_workshop_rows(client, limit=limit)
        if rows:
            return rows
        resp.headers["X-Data-Source"] = "mock"
        return []

    rows: list = []
    source = "mock"
    adapter = cfg["adapter"]
    if adapter is not None:
        rows, source = await fetch_with_fallback(
            client, adapter, domain=_ADAPTER_DOMAIN.get(module, []),
            mock_key=cfg["mock"], limit=limit, project_id=project_id,
        )
    if not rows and cfg["mock"]:
        rows = await fetch_mock_rows(cfg["mock"])
    if source == "mock":
        resp.headers["X-Data-Source"] = "mock"
    return rows


@router.get("/modules/workshop/{wc_id}/workorders")
async def workshop_workorders(
    wc_id: int,
    client: ClientDep,
    limit: int = Query(200, ge=1, le=500),
):
    """生产车间下钻：某车间的工单列表（mrp.workorder）"""
    from app.services.adapters.business import WorkOrderAdapter
    from app.services.odoo.models import FIELDS_WORKORDER

    try:
        records = await client.search_read(
            "mrp.workorder", [("workcenter_id", "=", wc_id)], FIELDS_WORKORDER, limit=limit,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("workshop_workorders(%s) 失败: %s", wc_id, e)
        return []
    adapter = WorkOrderAdapter()
    return [adapter.to_row(r) for r in records]


@router.get("/modules/{module}/config")
async def module_config(module: str, client: ClientDep, resp: Response):
    cfg = _MODULES.get(module)
    if cfg is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=f"未知模块: {module}")
    # 动态统计（从 Odoo 实时计算）
    stats = await compute_module_stats(client, module)
    return {
        "id": module,
        "title": cfg["title"],
        "subtitle": "来自 Odoo 标准模块数据",
        "icon": "layers",
        "stats": stats,
        "focus": "",
        "workflow": [],
    }


async def _count(client, model: str, domain: list | None = None) -> int:
    try:
        return await client.search_count(model, domain or [])
    except Exception:
        return 0


async def compute_module_stats(client, module: str) -> list[list[str]]:
    """按模块从 Odoo 实时计算统计卡数据（label, value）"""
    try:
        if module == "procurement":
            total = await _count(client, "purchase.order")
            in_progress = await _count(client, "purchase.order", [("state", "in", ["purchase", "sent", "to approve"])])
            done = await _count(client, "purchase.order", [("state", "=", "done")])
            cancel = await _count(client, "purchase.order", [("state", "=", "cancel")])
            return [
                ["采购单", str(total)],
                ["采购中", str(in_progress)],
                ["已到货", str(done)],
                ["已取消", str(cancel)],
            ]
        if module == "logistics":
            total = await _count(client, "stock.picking")
            done = await _count(client, "stock.picking", [("state", "=", "done")])
            in_transit = await _count(client, "stock.picking", [("state", "in", ["assigned", "confirmed"])])
            waiting = await _count(client, "stock.picking", [("state", "in", ["draft", "waiting"])])
            carriers = await _count(client, "delivery.carrier", [("active", "=", True)])
            return [
                ["流转批次", str(total)],
                ["已签收", str(done)],
                ["运输中", str(in_transit)],
                ["待发运", str(waiting)],
                ["承运商", str(carriers)],
            ]
        if module == "inventory":
            total = await _count(client, "stock.quant")
            products = await _count(client, "product.product")
            return [
                ["物料数量", str(total)],
                ["产品种类", str(products)],
            ]
        if module == "people":
            total = await _count(client, "hr.employee")
            active = await _count(client, "hr.employee", [("active", "=", True)])
            return [
                ["员工总数", str(total)],
                ["在职", str(active)],
                ["离岗", str(total - active)],
            ]
        if module == "vendors":
            total = await _count(client, "res.partner", [("supplier_rank", ">", 0)])
            companies = await _count(client, "res.partner", [("supplier_rank", ">", 0), ("is_company", "=", True)])
            return [
                ["供应商数", str(total)],
                ["企业供应商", str(companies)],
            ]
        if module == "electrical":
            total = await _count(client, "mrp.production")
            progress = await _count(client, "mrp.production", [("state", "=", "progress")])
            done = await _count(client, "mrp.production", [("state", "=", "done")])
            cancel = await _count(client, "mrp.production", [("state", "=", "cancel")])
            return [
                ["工单总数", str(total)],
                ["生产中", str(progress)],
                ["已完成", str(done)],
                ["已取消", str(cancel)],
            ]
        if module == "delivery":
            total = await _count(client, "project.task")
            done = await _count(client, "project.task", [("state", "=", "1_done")])
            active = await _count(client, "project.task", [("state", "=", "01_in_progress")])
            return [
                ["任务总数", str(total)],
                ["已完成", str(done)],
                ["进行中", str(active)],
            ]
        if module == "mes":
            total = await _count(client, "project.task", ["|", "|", "|",
                ("name", "ilike", "MES"), ("name", "ilike", "WMS"),
                ("name", "ilike", "软件"), ("name", "ilike", "系统")])
            done = await _count(client, "project.task", ["|", "|", "|",
                ("name", "ilike", "MES"), ("name", "ilike", "WMS"),
                ("name", "ilike", "软件"), ("name", "ilike", "系统"),
                ("state", "=", "1_done")])
            return [
                ["MES 相关任务", str(total)],
                ["已完成", str(done)],
            ]
        if module == "commissioning":
            # 单 stats 无意义，让前端用通用表格直接展示验收清单
            return []
        if module == "field":
            total = await _count(client, "project.task", [("state", "!=", "1_done")])
            high = await _count(client, "project.task", [("state", "not in", ["1_done", "03_approved"])])
            return [
                ["活跃风险", str(total)],
            ]
        if module == "design":
            total = await _count(client, "mrp.bom", [("active", "=", True)])
            phantom = await _count(client, "mrp.bom", [("type", "=", "phantom"), ("active", "=", True)])
            subcontract = await _count(client, "mrp.bom", [("type", "=", "subcontract"), ("active", "=", True)])
            return [
                ["BOM 清单", str(total)],
                ["虚拟 BOM", str(phantom)],
                ["分包 BOM", str(subcontract)],
            ]
        # ---- B 组：新模块统计 ----
        if module == "sales":
            total = await _count(client, "sale.order", [("state", "not in", ["cancel"])])
            confirmed = await _count(client, "sale.order", [("state", "=", "sale")])
            done = await _count(client, "sale.order", [("state", "=", "done")])
            draft = await _count(client, "sale.order", [("state", "=", "draft")])
            return [
                ["销售订单", str(total)],
                ["已确认", str(confirmed)],
                ["已完成", str(done)],
                ["草稿", str(draft)],
            ]
        if module == "products":
            total = await _count(client, "product.template", [("active", "=", True)])
            categories = await _count(client, "product.category")
            storable = await _count(client, "product.template", [("type", "=", "product"), ("active", "=", True)])
            return [
                ["产品总数", str(total)],
                ["分类数", str(categories)],
                ["可存储", str(storable)],
            ]
        if module == "manufacturing":
            total = await _count(client, "mrp.workorder", [("state", "not in", ["cancel"])])
            progress = await _count(client, "mrp.workorder", [("state", "=", "progress")])
            done = await _count(client, "mrp.workorder", [("state", "=", "done")])
            pending = await _count(client, "mrp.workorder", [("state", "in", ["pending", "ready"])])
            return [
                ["车间工单", str(total)],
                ["生产中", str(progress)],
                ["已完成", str(done)],
                ["待开始", str(pending)],
            ]
        if module == "workshop":
            total = await _count(client, "mrp.workcenter", [("active", "=", True)])
            running = await _count(client, "mrp.workcenter", [("working_state", "=", "done"), ("active", "=", True)])
            blocked = await _count(client, "mrp.workcenter", [("working_state", "=", "blocked"), ("active", "=", True)])
            wo_progress = await _count(client, "mrp.workorder", [("state", "=", "progress")])
            return [
                ["车间总数", str(total)],
                ["生产运行", str(running)],
                ["阻塞", str(blocked)],
                ["在制工单", str(wo_progress)],
            ]
    except Exception as e:  # noqa: BLE001
        from app.config import get_settings as _gs
        if not _gs().USE_MOCK:
            logger.warning(f"compute_module_stats({module}) 失败: {e}")
    # 失败时返回空（前端降级到 cfg.stats）
    return []


async def fetch_inventory_grouped(client, limit: int = 200) -> list[dict]:
    """stock.quant 按产品聚合：产品级库存行（总数量 + 库位数 + 记录数）"""
    try:
        records = await client.search_read(
            "stock.quant", [],
            ["id", "product_id", "location_id", "quantity", "reserved_quantity"],
            limit=5000,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("fetch_inventory_grouped 失败: %s", e)
        return []

    groups: dict[int, dict] = {}
    for r in records:
        pid = r.get("product_id")
        key = pid[0] if isinstance(pid, (list, tuple)) and pid else r.get("id")
        name = pid[1] if isinstance(pid, (list, tuple)) and len(pid) > 1 else "物料"
        g = groups.setdefault(key, {"id": key, "name": name, "qty": 0.0, "locs": set(), "n": 0})
        g["qty"] += r.get("quantity") or 0
        loc = r.get("location_id")
        if isinstance(loc, (list, tuple)) and len(loc) > 1:
            g["locs"].add(loc[1])
        g["n"] += 1

    rows = []
    for key, g in sorted(groups.items(), key=lambda x: -x[1]["qty"])[:limit]:
        in_stock = g["qty"] > 0
        rows.append({
            "id": f"MAT-{key}",
            "name": g["name"],
            "status": "在库" if in_stock else "缺货",
            "tone": "success" if in_stock else "warning",
            "progress": None,
            "cells": [
                f"MAT-{key}",
                f"{g['qty']:g}",
                f"{len(g['locs'])} 库位",
                f"{g['n']} 条记录",
                "在库" if in_stock else "缺货",
            ],
            "fields": [
                ["物料", g["name"]],
                ["库存总量", f"{g['qty']:g}"],
                ["库位数量", str(len(g["locs"]))],
                ["库存记录数", str(g["n"])],
            ],
        })
    return rows


async def fetch_commissioning_items(client, limit: int = 200) -> list[dict]:
    """验收清单：mrp.production 已完成/待关闭工单（作为可验收项）"""
    try:
        records = await client.search_read(
            "mrp.production",
            [("state", "in", ["done", "to_close"])],
            ["id", "name", "state", "date_finished", "product_id", "user_id", "product_qty"],
            limit=limit,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("fetch_commissioning_items 失败: %s", e)
        return []

    rows = []
    for r in records:
        product = r.get("product_id")
        product_name = product[1] if isinstance(product, (list, tuple)) and len(product) > 1 else "产品"
        state = r.get("state")
        label = "待验收" if state == "to_close" else "已验收"
        tone = "warning" if state == "to_close" else "success"
        owner = r.get("user_id")
        owner_name = owner[1] if isinstance(owner, (list, tuple)) and len(owner) > 1 else "—"
        rows.append({
            "id": f"UAT-{r['id']}",
            "name": product_name,
            "status": label,
            "tone": tone,
            "progress": 100 if state == "done" else 90,
            "cells": [
                f"UAT-{r['id']}",
                r.get("name", "—"),
                f"{r.get('product_qty', 0):g} 台",
                owner_name,
                label,
            ],
            "fields": [
                ["工单号", r.get("name", "—")],
                ["产品", product_name],
                ["数量", f"{r.get('product_qty', 0):g}"],
                ["负责人", owner_name],
                ["完成日期", (r.get("date_finished") or "")[:10] or "—"],
                ["状态", label],
            ],
        })
    return rows


# ====================================================================
#  A 组下钻接口：BOM 子件 / 采购订单行 / 库位分布 / 收发流水
# ====================================================================

@router.get("/modules/design/bom/{bom_id}/lines")
async def bom_lines(
    bom_id: int,
    client: ClientDep,
    limit: int = Query(100, ge=1, le=300),
):
    """设计与图纸下钻：某 BOM 的子件清单（mrp.bom.line）"""
    from app.services.odoo.models import FIELDS_BOM_LINE

    try:
        records = await client.search_read(
            "mrp.bom.line", [("bom_id", "=", bom_id)], FIELDS_BOM_LINE, limit=limit,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("bom_lines(%s) 失败: %s", bom_id, e)
        return []
    rows = []
    for r in records:
        product = r.get("product_id")
        product_name = product[1] if isinstance(product, (list, tuple)) and len(product) > 1 else "—"
        uom = r.get("product_uom_id")
        uom_name = uom[1] if isinstance(uom, (list, tuple)) and len(uom) > 1 else ""
        op = r.get("operation_id")
        op_name = op[1] if isinstance(op, (list, tuple)) and len(op) > 1 else "—"
        rows.append({
            "id": f"BL-{r['id']}",
            "name": product_name,
            "cells": [
                f"BL-{r['id']}",
                product_name,
                f"{r.get('product_qty', 0):g} {uom_name}".strip(),
                op_name,
            ],
            "status": "子件",
            "tone": "neutral",
            "fields": [
                ["子件", product_name],
                ["数量", f"{r.get('product_qty', 0):g} {uom_name}".strip()],
                ["工序", op_name],
                ["排序", str(r.get("sequence", 0))],
            ],
        })
    return rows


@router.get("/modules/procurement/order/{order_id}/lines")
async def procurement_lines(
    order_id: int,
    client: ClientDep,
    limit: int = Query(200, ge=1, le=500),
):
    """采购与交期下钻：某采购订单的订单行明细（purchase.order.line）"""
    from app.services.odoo.models import FIELDS_PURCHASE_LINE

    try:
        records = await client.search_read(
            "purchase.order.line", [("order_id", "=", order_id)], FIELDS_PURCHASE_LINE, limit=limit,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("procurement_lines(%s) 失败: %s", order_id, e)
        return []
    rows = []
    for r in records:
        product = r.get("product_id")
        product_name = product[1] if isinstance(product, (list, tuple)) and len(product) > 1 else "—"
        uom = r.get("product_uom")
        uom_name = uom[1] if isinstance(uom, (list, tuple)) and len(uom) > 1 else ""
        qty = r.get("product_qty", 0)
        received = r.get("qty_received", 0)
        line_state = r.get("state", "")
        label = {
            "draft": "草稿", "sent": "已发送", "to approve": "待审批",
            "approved": "已批准", "purchase": "已下单", "done": "已完成",
            "cancel": "已取消",
        }.get(line_state, line_state)
        received_ratio = min(100, round(received / qty * 100)) if qty else 0
        return_row = {
            "id": f"PL-{r['id']}",
            "name": product_name,
            "cells": [
                f"PL-{r['id']}",
                product_name,
                f"{qty:g} {uom_name}".strip(),
                f"{received:g}",
                f"{r.get('price_unit', 0):,.2f}",
                label,
            ],
            "status": label,
            "tone": "success" if received >= qty and qty else ("warning" if received > 0 else "neutral"),
            "fields": [
                ["物料", product_name],
                ["订购数量", f"{qty:g} {uom_name}".strip()],
                ["已收数量", f"{received:g}"],
                ["已开票", f"{(r.get('qty_invoiced') or 0):g}"],
                ["单价", f"{r.get('price_unit', 0):,.2f}"],
                ["计划到货", (r.get("date_planned") or "")[:10] or "—"],
                ["状态", label],
            ],
        }
        if received_ratio is not None:
            return_row["progress"] = received_ratio
        rows.append(return_row)
    return rows


@router.get("/modules/inventory/locations")
async def inventory_locations(
    client: ClientDep,
    limit: int = Query(200, ge=1, le=500),
):
    """现场库存：库位分布（stock.location 内部库位 + 该库位 quant 汇总）"""
    try:
        locations = await client.search_read(
            "stock.location", [("usage", "=", "internal")],
            ["id", "complete_name", "usage"], limit=2000,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("inventory_locations 失败: %s", e)
        return []

    # 每个库位下 quant 汇总（物料种类 + 总数量）
    try:
        quants = await client.search_read(
            "stock.quant", [],
            ["id", "location_id", "quantity", "product_id"], limit=5000,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("inventory_locations quant 失败: %s", e)
        quants = []

    loc_map: dict[int, dict] = {}
    for loc in locations:
        lid = loc["id"]
        loc_map[lid] = {
            "id": f"LOC-{lid}",
            "name": loc.get("complete_name") or "库位",
            "kinds": set(),
            "qty": 0.0,
        }
    for q in quants:
        lref = q.get("location_id")
        lid = lref[0] if isinstance(lref, (list, tuple)) and lref else None
        g = loc_map.get(lid)
        if not g:
            continue
        g["qty"] += q.get("quantity") or 0
        g["kinds"].add(q.get("product_id")[0] if isinstance(q.get("product_id"), (list, tuple)) and q.get("product_id") else q.get("id"))

    rows = []
    for g in sorted(loc_map.values(), key=lambda x: -x["qty"])[:limit]:
        rows.append({
            "id": g["id"],
            "name": g["name"],
            "status": "有库存" if g["qty"] > 0 else "空库位",
            "tone": "success" if g["qty"] > 0 else "neutral",
            "cells": [
                g["id"],
                g["name"],
                f"{len(g['kinds'])} 种",
                f"{g['qty']:g}",
            ],
            "fields": [
                ["库位", g["name"]],
                ["物料种类", str(len(g["kinds"]))],
                ["总数量", f"{g['qty']:g}"],
            ],
        })
    return rows


@router.get("/modules/inventory/moves")
async def inventory_moves(
    client: ClientDep,
    limit: int = Query(100, ge=1, le=300),
):
    """现场库存：最近收发流水（stock.move，按日期倒序）"""
    from app.services.odoo.models import FIELDS_STOCK_MOVE

    try:
        records = await client.search_read(
            "stock.move", [], FIELDS_STOCK_MOVE, limit=limit, order="date desc, id desc",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("inventory_moves 失败: %s", e)
        return []
    rows = []
    for r in records:
        product = r.get("product_id")
        product_name = product[1] if isinstance(product, (list, tuple)) and len(product) > 1 else "—"
        src = r.get("location_id")
        src_name = src[1] if isinstance(src, (list, tuple)) and len(src) > 1 else "—"
        dst = r.get("location_dest_id")
        dst_name = dst[1] if isinstance(dst, (list, tuple)) and len(dst) > 1 else "—"
        state = r.get("state", "")
        is_in = "internal" in src_name and "internal" not in dst_name or False
        label = {
            "draft": "草稿", "waiting": "等待", "confirmed": "已确认",
            "assigned": "已分配", "done": "已完成", "cancel": "已取消",
        }.get(state, state)
        rows.append({
            "id": f"MV-{r['id']}",
            "name": product_name,
            "status": label,
            "tone": "success" if state == "done" else ("warning" if state in ("assigned", "confirmed") else "neutral"),
            "cells": [
                f"MV-{r['id']}",
                product_name,
                src_name,
                dst_name,
                f"{r.get('quantity', r.get('product_qty', 0)):g}",
                (r.get("date") or "")[:10],
                label,
            ],
            "fields": [
                ["物料", product_name],
                ["源库位", src_name],
                ["目的库位", dst_name],
                ["数量", f"{r.get('quantity', r.get('product_qty', 0)):g}"],
                ["日期", (r.get("date") or "")[:16].replace("T", " ") or "—"],
                ["参考单据", r.get("reference") or "—"],
                ["状态", label],
            ],
        })
    return rows


async def fetch_workshop_rows(client, limit: int = 200) -> list[dict]:
    """生产车间：mrp.workcenter 列表 + 每个车间在制工单数（mrp.workorder 聚合）"""
    from app.services.odoo.models import FIELDS_WORKCENTER
    from app.services.adapters.business import WorkcenterAdapter

    try:
        records = await client.search_read(
            "mrp.workcenter", [("active", "=", True)], FIELDS_WORKCENTER, limit=1000,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("fetch_workshop_rows workcenter 失败: %s", e)
        return []
    if not records:
        return []

    # 聚合在制工单（按 workcenter_id）
    try:
        wos = await client.search_read(
            "mrp.workorder", [("state", "=", "progress")],
            ["id", "workcenter_id"], limit=5000,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("fetch_workshop_rows workorder 失败: %s", e)
        wos = []

    progress_by: dict[int, int] = {}
    for w in wos:
        wc = w.get("workcenter_id")
        cid = wc[0] if isinstance(wc, (list, tuple)) and wc else None
        if cid:
            progress_by[cid] = progress_by.get(cid, 0) + 1

    adapter = WorkcenterAdapter()
    rows = []
    for r in records:
        row = adapter.to_row(r)
        n_progress = progress_by.get(r["id"], 0)
        row["cells"] = row["cells"] + [f"{n_progress} 单"]
        row["fields"] = row["fields"] + [["在制工单", f"{n_progress} 单"]]
        rows.append(row)
    return rows[:limit]
