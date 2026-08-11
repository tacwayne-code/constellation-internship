"""一键生成紧急采购订单（对"需采购"配件，写入 Odoo 标准模型）

流程：
  1. analyze_delivery → 找 need_purchase 的配件
  2. 每个配件取默认供应商（product.supplierinfo，按 product.template）
  3. create purchase.order（priority='1' 紧急 + order_line）+ date_planned = 今天 + 供应商交期
  4. 无供应商/失败 → 跳过并记录原因
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from app.services.delivery_analysis import _ref_id, _ref_name, analyze_delivery
from app.services.odoo.client import OdooClient

logger = logging.getLogger(__name__)


def _eta_plus_delay(delay_days: int) -> str:
    """预计到货日 = 今天 + 供应商交期（ISO 日期）"""
    return (date.today() + timedelta(days=max(int(delay_days or 0), 1))).isoformat()


async def create_urgent_purchases(client: OdooClient, so_id: int) -> dict[str, Any]:
    """对需采购配件生成紧急采购单。返回 {so_name, created, skipped, note}。"""
    data = await analyze_delivery(client, so_id)
    if "error" in data:
        return data

    need = [m for m in data["materials"] if m["need_purchase"]]
    if not need:
        return {"so_name": data["so"]["name"], "created": [], "skipped": [], "note": "无待采购配件"}

    # ── 默认供应商（product.supplierinfo，template 维度） ──
    tmpl_ids = list({m["product_tmpl_id"] for m in need if m.get("product_tmpl_id")})
    supplier_map: dict[int, list[dict]] = {}
    if tmpl_ids:
        sellers = await client.search_read(
            "product.supplierinfo",
            [["product_tmpl_id", "in", tmpl_ids], ["partner_id", "!=", False]],
            ["id", "product_tmpl_id", "partner_id", "price", "delay"], limit=200,
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

    created = []
    skipped = []
    for m in need:
        suppliers = supplier_map.get(m.get("product_tmpl_id"), [])
        if not suppliers:
            skipped.append({"product": m["product"], "reason": "无默认供应商"})
            continue
        seller = suppliers[0]
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
            logger.info("SO %s 紧急采购: %s %s ×%s", data["so"]["name"], po[0]["name"] if po else po_id, m["product"], qty)
        except Exception as e:  # noqa: BLE001
            logger.exception("create urgent PO failed for %s", m["product"])
            skipped.append({"product": m["product"], "reason": str(e)[:120]})

    return {
        "so_name": data["so"]["name"],
        "created": created,
        "skipped": skipped,
        "note": f"已生成 {len(created)} 张紧急采购单，跳过 {len(skipped)} 件",
    }
