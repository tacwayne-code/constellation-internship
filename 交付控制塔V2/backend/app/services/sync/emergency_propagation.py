"""紧急继承 sync：销售订单带"紧急"标签 → PO/MO 自动标记 priority=1

两级传播（不改 Odoo 模块，纯靠交付塔后端调度写回标准 priority 字段）：
  1. 直接关联：SO 销售行 → purchase.order.line.sale_line_id → PO；SO 名 → MO.origin
  2. BOM 配件级：订单产品 → mrp.bom → 子件(配件) → 子件相关 PO / MO（"所需配件也为紧急"）

调用入口：await propagate_emergency(client) → 返回受影响统计 dict
"""
from __future__ import annotations

import logging
import re
from typing import Any

from app.services.odoo.client import OdooClient
from app.services.odoo.models import (
    MODEL_BOM,
    MODEL_BOM_LINE,
    MODEL_CRM_TAG,
    MODEL_MRP_PRODUCTION,
    MODEL_PURCHASE,
    MODEL_PURCHASE_LINE,
    MODEL_SALE_ORDER,
    MODEL_SALE_ORDER_LINE,
    PRIORITY_URGENT,
    TAG_NAMES_EMERGENCY,
)

logger = logging.getLogger(__name__)


def _ref_id(ref: Any) -> int | None:
    """兼容 Odoo 18 m2o：返回 id 或 None"""
    if isinstance(ref, (list, tuple)):
        return ref[0] if ref else None
    if isinstance(ref, int):
        return ref
    return None


def _has_tag(tag_ids: list, tag_id: int) -> bool:
    """兼容 Odoo 18 m2m 返回纯 id 列表 [1] 或 (id, name) 元组"""
    for t in tag_ids or []:
        if isinstance(t, (list, tuple)):
            if t and t[0] == tag_id:
                return True
        elif t == tag_id:
            return True
    return False


async def _get_emergency_tag_ids(client: OdooClient) -> list[int]:
    tags = await client.search_read(
        MODEL_CRM_TAG,
        [["name", "in", list(TAG_NAMES_EMERGENCY)]],
        ["id", "name"],
    )
    return [t["id"] for t in tags]


async def _find_emergency_sale_orders(client: OdooClient, tag_ids: list[int]) -> list[dict]:
    if not tag_ids:
        return []
    return await client.search_read(
        MODEL_SALE_ORDER,
        [
            ["tag_ids", "in", tag_ids],
            ["state", "not in", ["cancel"]],
        ],
        ["id", "name", "state", "tag_ids"],
        order="write_date desc, id desc",
        limit=500,
    )


async def _collect_linked_so_ids(client: OdooClient, pos: list[dict]) -> set[int]:
    """双链路反查：PO 列表 → 关联 SO id 集合（只读，不写数据）

    链路 A：purchase.order.line.sale_line_id → sale.order.line → sale.order
    链路 B：purchase.order.origin 中包含 sale.order.name（兼容逗号/斜杠/分号拼接）
    """
    so_ids: set[int] = set()

    # 链路 A
    line_ids = [lid for p in pos for lid in (p.get("order_line") or [])]
    if line_ids:
        pols = await client.search_read(
            MODEL_PURCHASE_LINE,
            [["id", "in", line_ids], ["sale_line_id", "!=", False]],
            ["id", "sale_line_id"], limit=None,
        )
        sl_ids = [
            r["sale_line_id"][0]
            for r in pols
            if isinstance(r.get("sale_line_id"), (list, tuple)) and r["sale_line_id"]
        ]
        if sl_ids:
            sols = await client.search_read(
                MODEL_SALE_ORDER_LINE, [["id", "in", sl_ids]],
                ["id", "order_id"], limit=None,
            )
            for s in sols:
                oid = _ref_id(s.get("order_id"))
                if oid:
                    so_ids.add(oid)

    # 链路 B
    origins: set[str] = set()
    for p in pos:
        for part in re.split(r"[,/;]", p.get("origin") or ""):
            part = part.strip()
            if part:
                origins.add(part)
    if origins:
        sos = await client.search_read(
            MODEL_SALE_ORDER, [["name", "in", list(origins)]], ["id"], limit=None,
        )
        so_ids.update(s["id"] for s in sos)

    return so_ids


async def _reverse_to_sale(client: OdooClient) -> dict[str, Any]:
    """反向传播：PO priority=1 → 关联 SO 打「紧急」tag（正向链路的触发源）

    关联规则（与 aggregate_order 双链路一致）：
      A. purchase.order.line.sale_line_id → sale.order.line → sale.order
      B. purchase.order.origin 中包含 sale.order.name
    幂等：已有「紧急」tag 的 SO 跳过。
    返回 {"found_sos": N, "tagged": N, "errors": [...]}
    """
    result: dict[str, Any] = {"found_sos": 0, "tagged": 0, "errors": []}

    # 1. 所有 priority=1 且未取消的 PO
    pos = await client.search_read(
        MODEL_PURCHASE,
        [["priority", "=", PRIORITY_URGENT], ["state", "not in", ["cancel"]]],
        ["id", "name", "origin", "order_line"], limit=500,
    )
    if not pos:
        return result

    # 2. 双链路反查 SO id
    so_ids = await _collect_linked_so_ids(client, pos)
    result["found_sos"] = len(so_ids)
    if not so_ids:
        return result

    # 3. 给 SO 打「紧急」tag（幂等：已有则跳过）
    tag_ids = await _get_emergency_tag_ids(client)
    if not tag_ids:
        result["errors"].append("crm.tag 未建（请先运行 python -m scripts.init_crm_tags）")
        return result
    tag_id = tag_ids[0]

    sos = await client.search_read(
        MODEL_SALE_ORDER, [["id", "in", list(so_ids)]], ["id", "name", "tag_ids"], limit=None,
    )
    for so in sos:
        if _has_tag(so["tag_ids"], tag_id):
            continue
        await client.write(MODEL_SALE_ORDER, [so["id"]], {"tag_ids": [(4, tag_id)]})
        result["tagged"] += 1
        logger.info("PO 紧急反向: SO %s(%s) 打「紧急」tag", so["id"], so.get("name"))

    return result


async def _propagate_to_purchase(client: OdooClient, so_id: int, so_name: str) -> int:
    """通过 sale_line_id 找到该 SO 关联的 PO，把 priority!=3 的写为 3。返回受影响数。"""
    sol_ids = [
        r["id"] for r in await client.search_read(
            MODEL_SALE_ORDER_LINE, [["order_id", "=", so_id]], ["id"], limit=None,
        )
    ]
    if not sol_ids:
        return 0

    pols = await client.search_read(
        MODEL_PURCHASE_LINE,
        [["sale_line_id", "in", sol_ids]],
        ["id", "order_id"],
        limit=None,
    )
    po_ids = list({p["order_id"][0] for p in pols if p.get("order_id")})
    if not po_ids:
        return 0

    # 只更新 priority 不等于 3 的
    pos = await client.search_read(
        MODEL_PURCHASE,
        [["id", "in", po_ids], ["priority", "!=", PRIORITY_URGENT]],
        ["id", "priority"],
        limit=None,
    )
    targets = [p["id"] for p in pos]
    if not targets:
        return 0

    await client.write(MODEL_PURCHASE, targets, {"priority": PRIORITY_URGENT})
    logger.info("SO %s → PO %s priority→%s", so_name, targets, PRIORITY_URGENT)
    return len(targets)


async def _propagate_to_production(client: OdooClient, so_name: str) -> int:
    """通过 origin=SO 名找到关联的 mrp.production，写 priority=1。返回受影响数。"""
    mos = await client.search_read(
        MODEL_MRP_PRODUCTION,
        [
            ["origin", "=", so_name],
            ["priority", "!=", PRIORITY_URGENT],
            ["state", "not in", ["cancel"]],
        ],
        ["id", "name", "priority"],
        limit=None,
    )
    targets = [m["id"] for m in mos]
    if not targets:
        return 0
    await client.write(MODEL_MRP_PRODUCTION, targets, {"priority": PRIORITY_URGENT})
    logger.info("SO %s → MO %s priority→%s", so_name, targets, PRIORITY_URGENT)
    return len(targets)


async def _collect_component_product_ids(client: OdooClient, so_id: int) -> tuple[list[int], list[int]]:
    """订单产品 → BOM → 子件(配件) product.product id 列表。

    返回 (tmpl_ids, component_variant_ids)：
      tmpl_ids: 订单产品对应的 product.template id（查 BOM 用）
      component_variant_ids: BOM 子件的 product.product id（查 PO/MO 用）
    """
    sols = await client.search_read(
        MODEL_SALE_ORDER_LINE, [["order_id", "=", so_id]],
        ["id", "product_id"], limit=None,
    )
    variant_ids = list({
        p["product_id"][0] for p in sols
        if isinstance(p.get("product_id"), (list, tuple)) and p["product_id"]
    })
    if not variant_ids:
        return [], []

    # variant → template id（跨模型 id 空间不同，需要转换）
    products = await client.search_read(
        "product.product", [["id", "in", variant_ids]],
        ["id", "product_tmpl_id"], limit=None,
    )
    tmpl_ids = list({p["product_tmpl_id"][0] for p in products if p.get("product_tmpl_id")})
    if not tmpl_ids:
        return [], []

    boms = await client.search_read(
        MODEL_BOM, [["product_tmpl_id", "in", tmpl_ids]],
        ["id", "product_tmpl_id", "bom_line_ids"], limit=None,
    )
    line_ids = [lid for b in boms for lid in (b["bom_line_ids"] or [])]
    if not line_ids:
        return tmpl_ids, []

    lines = await client.search_read(
        MODEL_BOM_LINE, [["id", "in", line_ids]],
        ["id", "product_id"], limit=None,
    )
    comp_ids = list({
        l["product_id"][0] for l in lines
        if isinstance(l.get("product_id"), (list, tuple)) and l["product_id"]
    })
    return tmpl_ids, comp_ids


async def _propagate_to_components(client: OdooClient, so_id: int, so_name: str) -> tuple[int, int]:
    """BOM 配件级传播：订单产品 → BOM 子件 → 子件相关 PO / MO 标 priority=1。

    返回 (affected_po, affected_mo)。
    """
    tmpl_ids, comp_ids = await _collect_component_product_ids(client, so_id)
    if not comp_ids:
        return 0, 0

    # 子件相关采购单（该配件的采购行所在 PO）
    pols = await client.search_read(
        MODEL_PURCHASE_LINE,
        [["product_id", "in", comp_ids], ["state", "not in", ["cancel"]]],
        ["id", "order_id"], limit=None,
    )
    po_ids = list({p["order_id"][0] for p in pols if p.get("order_id")})
    affected_po = 0
    if po_ids:
        pos = await client.search_read(
            MODEL_PURCHASE,
            [["id", "in", po_ids], ["priority", "!=", PRIORITY_URGENT]],
            ["id", "name", "priority"], limit=None,
        )
        targets = [p["id"] for p in pos]
        if targets:
            await client.write(MODEL_PURCHASE, targets, {"priority": PRIORITY_URGENT})
            affected_po = len(targets)
            logger.info("SO %s 配件 → PO %s priority→%s", so_name, [p["name"] for p in pos], PRIORITY_URGENT)

    # 子件相关生产单（该配件的 MO）
    mos = await client.search_read(
        MODEL_MRP_PRODUCTION,
        [
            ["product_id", "in", comp_ids],
            ["priority", "!=", PRIORITY_URGENT],
            ["state", "not in", ["cancel"]],
        ],
        ["id", "name", "priority"], limit=None,
    )
    affected_mo = 0
    targets = [m["id"] for m in mos]
    if targets:
        await client.write(MODEL_MRP_PRODUCTION, targets, {"priority": PRIORITY_URGENT})
        affected_mo = len(targets)
        logger.info("SO %s 配件 → MO %s priority→%s", so_name, [m["name"] for m in mos], PRIORITY_URGENT)

    return affected_po, affected_mo


async def propagate_emergency(client: OdooClient) -> dict[str, Any]:
    """主入口：扫描带紧急 tag 的销售订单，把关联 PO/MO 标 priority=1。

    返回：
      {
        "emergency_tags": [1, 2],
        "emergency_sales": [...so_name...],
        "affected_po": N,
        "affected_mo": M,
        "affected_comp_po": N,   # BOM 配件级：配件采购单数
        "affected_comp_mo": N,   # BOM 配件级：配件生产单数
        "errors": ["..."]
      }
    """
    result: dict[str, Any] = {
        "emergency_tags": [],
        "emergency_sales": [],
        "affected_po": 0,
        "affected_mo": 0,
        "affected_comp_po": 0,
        "affected_comp_mo": 0,
        "errors": [],
    }

    tag_ids = await _get_emergency_tag_ids(client)
    if not tag_ids:
        result["errors"].append("crm.tag 未建（请先运行 python -m scripts.init_crm_tags）")
        return result
    result["emergency_tags"] = tag_ids

    # 反向传播先行：PO priority=1 → 关联 SO 打「紧急」tag，保证双向闭环
    # 之后正向扫描会把新打标的 SO 一并纳入（幂等，无循环）
    result["reverse"] = await _reverse_to_sale(client)

    sos = await _find_emergency_sale_orders(client, tag_ids)
    result["emergency_sales"] = [s["name"] for s in sos]
    if not sos:
        return result

    for so in sos:
        try:
            result["affected_po"] += await _propagate_to_purchase(client, so["id"], so["name"])
            result["affected_mo"] += await _propagate_to_production(client, so["name"])
            # BOM 配件级：订单所需配件（BOM 子件）相关的 PO/MO 也标紧急
            cpo, cmo = await _propagate_to_components(client, so["id"], so["name"])
            result["affected_comp_po"] += cpo
            result["affected_comp_mo"] += cmo
        except Exception as e:  # noqa: BLE001
            result["errors"].append(f"{so['name']}: {e}")
            logger.exception("propagate %s failed", so["name"])

    return result