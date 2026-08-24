"""一键生成紧急采购订单（对"需采购"配件，写入 Odoo 标准模型）

流程：
  1. analyze_delivery → 找 need_purchase 的配件（缺口>0 且无在途且无已存在 PO）
  2. 每个配件列出候选供应商（product.supplierinfo，按 template 维度）：
     - get_urgent_purchase_options()：返回全量供应商供前端选择
     - create_urgent_purchases(vendors=...)：按前端选择生成，缺省用第一条（默认供应商）
  3. create purchase.order（priority='1' 紧急 + order_line）+ date_planned = 今天 + 供应商交期
  4. 无供应商/失败 → 跳过并记录原因
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from app.services.cache import get_cache
from app.services.delivery_analysis import _ref_id, _ref_name, analyze_delivery
from app.services.odoo.client import OdooClient

logger = logging.getLogger(__name__)


def _eta_plus_delay(delay_days: int) -> str:
    """预计到货日 = 今天 + 供应商交期（ISO 日期）"""
    return (date.today() + timedelta(days=max(int(delay_days or 0), 1))).isoformat()


async def _supplier_map(client: OdooClient, tmpl_ids: list[int]) -> dict[int, list[dict]]:
    """按 product.template 收集全量供应商（按交期短优先排序，首条为默认）"""
    supplier_map: dict[int, list[dict]] = {}
    if not tmpl_ids:
        return supplier_map
    sellers = await client.search_read(
        "product.supplierinfo",
        [["product_tmpl_id", "in", tmpl_ids], ["partner_id", "!=", False]],
        ["id", "product_tmpl_id", "partner_id", "price", "delay"], limit=500,
    )
    for s in sellers:
        tmpl = s.get("product_tmpl_id")
        if isinstance(tmpl, (list, tuple)) and tmpl:
            supplier_map.setdefault(tmpl[0], []).append({
                "partner_id": _ref_id(s.get("partner_id")),
                "partner_name": _ref_name(s.get("partner_id")),
                "price": s.get("price") or 0,
                "delay": s.get("delay") or 0,
            })
    for lst in supplier_map.values():
        lst.sort(key=lambda x: (x["delay"], x["partner_id"]))
    return supplier_map


async def get_urgent_purchase_options(client: OdooClient, so_id: int) -> dict[str, Any]:
    """需采购配件的供应商候选列表（供前端选择）。

    返回 {so_name, items: [{product_id, product, qty, suppliers: [{partner_id, partner_name, price, delay}]}]}
    """
    data = await analyze_delivery(client, so_id)
    if "error" in data:
        return data

    need = [m for m in data["materials"] if m["need_purchase"]]
    tmpl_ids = list({m["product_tmpl_id"] for m in need if m.get("product_tmpl_id")})
    supplier_map = await _supplier_map(client, tmpl_ids)

    items = []
    for m in need:
        items.append({
            "product_id": m["product_id"],
            "product": m["product"],
            "qty": max(m["gap"], 1),
            "suppliers": supplier_map.get(m.get("product_tmpl_id"), []),
        })
    return {"so_name": data["so"]["name"], "items": items}


async def create_urgent_purchases(
    client: OdooClient,
    so_id: int,
    vendors: dict[int, int] | None = None,
) -> dict[str, Any]:
    """对需采购配件生成紧急采购单。

    vendors: {product_id: partner_id} —— 前端选择的供应商；缺省/未指定则用默认供应商（第一条）。
    返回 {so_name, created, skipped, note}。
    """
    data = await analyze_delivery(client, so_id)
    if "error" in data:
        return data

    need = [m for m in data["materials"] if m["need_purchase"]]
    if not need:
        return {"so_name": data["so"]["name"], "created": [], "skipped": [], "note": "无待采购配件"}

    tmpl_ids = list({m["product_tmpl_id"] for m in need if m.get("product_tmpl_id")})
    supplier_map = await _supplier_map(client, tmpl_ids)

    # 供应商定案：优先 vendors 指定，否则默认第一条；无供应商的配件进入 skipped
    chosen: dict[int, dict[str, Any]] = {}
    no_supplier: list[dict[str, Any]] = []
    for m in need:
        suppliers = supplier_map.get(m.get("product_tmpl_id"), [])
        if not suppliers:
            no_supplier.append({"product": m["product"], "reason": "无默认供应商"})
            continue
        wanted = (vendors or {}).get(m["product_id"])
        seller = next((s for s in suppliers if s["partner_id"] == wanted), suppliers[0])
        chosen[m["product_id"]] = {"material": m, "seller": seller}

    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = list(no_supplier)
    for pid, rec in chosen.items():
        m, seller = rec["material"], rec["seller"]
        qty = max(m["gap"], 1)
        try:
            po_id = await client.create("purchase.order", {
                "partner_id": seller["partner_id"],
                "priority": "1",  # 紧急
                "origin": data["so"]["name"],
                "order_line": [(0, 0, {
                    "product_id": m["product_id"],
                    "product_qty": qty,
                    "price_unit": seller["price"],
                    "date_planned": _eta_plus_delay(seller["delay"]),
                })],
            })
            po = await client.search_read("purchase.order", [["id", "=", po_id]], ["name", "state"], limit=1)
            created.append({
                "po_id": po_id,
                "po_name": po[0]["name"] if po else f"PO{po_id}",
                "product": m["product"],
                "qty": qty,
                "partner": seller["partner_name"],
                "state": po[0]["state"] if po else "draft",
            })
            logger.info("SO %s 紧急采购: %s %s ×%s (vendor=%s)", data["so"]["name"],
                        po[0]["name"] if po else po_id, m["product"], qty, seller["partner_name"])
        except Exception as e:  # noqa: BLE001
            logger.exception("create urgent PO failed for %s", m["product"])
            skipped.append({"product": m["product"], "reason": str(e)[:120]})

    # 建单后失效 analyze_delivery 缓存，避免 30s 内重复点「一键采购」命中旧结果重复建单
    get_cache().delete(f"delivery_analysis:{so_id}")

    return {
        "so_name": data["so"]["name"],
        "created": created,
        "skipped": skipped,
        "note": f"已生成 {len(created)} 张紧急采购单，跳过 {len(skipped)} 件",
    }
