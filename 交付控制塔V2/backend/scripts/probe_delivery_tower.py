"""交付塔需求可行性探测脚本（只读，不写任何数据）

验证目标：
1. Odoo 版本 / 认证 / 已安装模块（delivery、mrp、purchase 等）
2. 需求涉及的关键字段是否存在：priority / tag_ids / carrier_id / sale_line_id / bom
3. 数据链路抽样：sale.order → purchase.order.line(sale_line_id) → stock.picking(carrier) → mrp.production(origin)

用法（backend/ 目录）：
    python -m scripts.probe_delivery_tower
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import xmlrpc.client  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.services.odoo.client import OdooClient  # noqa: E402


async def main() -> int:
    settings = get_settings()
    client = OdooClient.get_instance(settings)

    print(f"[目标] {settings.ODOO_URL} | db={settings.ODOO_DB} | user={settings.ODOO_USER}")

    # ── 1. 版本 + 认证 ──
    try:
        common = xmlrpc.client.ServerProxy(f"{settings.ODOO_URL}/xmlrpc/2/common")
        version = common.version()
        print(f"\n[1] Odoo 版本: {version.get('server_version')} (serie {version.get('server_serie')})")
    except Exception as e:  # noqa: BLE001
        print(f"\n[1] 端点不可达: {e}")
        return 1

    try:
        uid = await client.authenticate()
        print(f"[1] 认证成功 uid={uid} (user={settings.ODOO_USER})")
    except Exception as e:  # noqa: BLE001
        print(f"[1] 认证失败: {e}")
        return 1

    # ── 2. 已安装模块 ──
    mods = await client.search_read(
        "ir.module.module", [["state", "=", "installed"]], ["name", "shortdesc"]
    )
    installed = {m["name"]: m["shortdesc"] for m in mods}
    targets = [
        "sale_management", "purchase", "stock", "mrp", "delivery",
        "sale", "project", "account", "stock_delivery",
    ]
    print(f"\n[2] 已安装模块共 {len(installed)} 个，关键模块：")
    for t in targets:
        mark = "✔" if t in installed else "✘"
        print(f"    {mark} {t}" + (f" — {installed[t]}" if t in installed else " (未安装)"))

    # ── 3. 关键字段探测 ──
    checks = {
        "sale.order": ["priority", "tag_ids", "state", "commitment_date", "order_line", "project_id"],
        "sale.order.line": ["product_id", "qty_delivered", "qty_to_deliver", "product_uom_qty", "order_id"],
        "purchase.order": ["priority", "tag_ids", "state", "date_planned", "project_id", "partner_id"],
        "purchase.order.line": ["sale_line_id", "order_id", "product_id", "state", "date_planned"],
        "stock.picking": ["carrier_id", "carrier_tracking_ref", "route_id", "project_id", "origin", "state"],
        "mrp.production": ["priority", "bom_id", "origin", "state", "product_id", "product_qty"],
        "mrp.bom": ["product_tmpl_id", "product_id", "bom_line_ids", "type", "code"],
        "mrp.bom.line": ["product_id", "product_qty", "bom_id"],
        "delivery.carrier": ["name", "delivery_type", "active"],
        "stock.quant": ["product_id", "location_id", "quantity", "reserved_quantity"],
    }
    print("\n[3] 字段探测（有/无）：")
    for model, fields in checks.items():
        try:
            specs = await client.execute_kw(model, "fields_get", [[], fields])
            present = sorted(f for f in fields if f in specs)
            missing = sorted(f for f in fields if f not in specs)
            print(f"    {model}:")
            print(f"      有: {present}")
            if missing:
                print(f"      无: {missing}")
        except Exception as e:  # noqa: BLE001
            print(f"    {model}: 读取失败 -> {e}")

    # ── 4. 数据抽样 ──
    print("\n[4] 数据抽样：")

    so = await client.search_read(
        "sale.order", [], ["id", "name", "state", "tag_ids"], limit=500
    )
    n_t = sum(1 for s in so if s.get("tag_ids"))
    print(f"    sale.order 抽样 {len(so)} 条 | 带标签 {n_t} 条")
    if n_t:
        for s in so:
            if s.get("tag_ids"):
                print(f"      {s['name']} state={s['state']} tags={[t[1] for t in s['tag_ids']]}")
                break

    # crm.tag 标签表（sale.order 的标签模型）
    try:
        tags = await client.search_read("crm.tag", [], ["id", "name", "color"], limit=50)
        print(f"    crm.tag（销售订单标签）共 {len(tags)} 个: {[t['name'] for t in tags[:15]]}")
    except Exception as e:  # noqa: BLE001
        print(f"    crm.tag 读取失败: {e}")

    po = await client.search_read(
        "purchase.order", [], ["id", "name", "state", "priority"], limit=500
    )
    n_u = sum(1 for s in po if (s.get("priority") or "") == "1")
    n_h = sum(1 for s in po if (s.get("priority") or 0) == 2)
    print(f"    purchase.order 抽样 {len(po)} 条 | priority=3(紧急) {n_u} | =2(高) {n_h}")
    if n_u:
        for p in po:
            if (p.get("priority") or "") == "1":
                print(f"      例: {p['name']} state={p['state']} priority={p['priority']}")

    # sale_line_id 反查链路
    pol = await client.search_read(
        "purchase.order.line", [["sale_line_id", "!=", False]],
        ["id", "order_id", "sale_line_id", "product_id", "state"], limit=30,
    )
    print(f"    purchase.order.line 带 sale_line_id（可反查销售来源）: 抽样 {len(pol)} 条")
    for p in pol[:6]:
        print(f"      line#{p['id']} → PO({p['order_id'][0]}) 源自 sale.line#{p['sale_line_id'][0]} state={p['state']}")

    # carrier 探测
    try:
        n_carrier = await client.search_count("delivery.carrier", [])
        n_pick = await client.search_count("stock.picking", [["carrier_id", "!=", False]])
        print(f"    delivery.carrier 共 {n_carrier} 家 | 拣货单已关联承运商 {n_pick} 条")
    except Exception as e:  # noqa: BLE001
        print(f"    delivery 探测失败: {e}")

    # mrp.production origin 关联 SO
    mrp = await client.search_read(
        "mrp.production", [["origin", "!=", False]],
        ["id", "name", "origin", "state", "bom_id", "priority", "product_id"], limit=50,
    )
    so_linked = [m for m in mrp if any(k in (m.get("origin") or "").upper() for k in ("SO", "销售", "SALE"))]
    print(f"    mrp.production 带 origin 抽样 {len(mrp)} 条 | 含销售来源 {len(so_linked)} 条")
    for m in so_linked[:6]:
        print(f"      {m['name']} origin={m['origin']} state={m['state']} priority={m.get('priority')} bom={m.get('bom_id')}")

    # 库存
    q = await client.search_read(
        "stock.quant", [["quantity", "!=", 0]],
        ["product_id", "location_id", "quantity", "reserved_quantity"], limit=5,
    )
    print(f"    stock.quant 非零数量抽样 {len(q)} 条:")
    for x in q:
        print(f"      {x['product_id'][1]} @ {x['location_id'][1]} qty={x['quantity']} reserved={x['reserved_quantity']}")

    print("\n✔ 探测完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
