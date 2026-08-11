"""紧急效果演示数据：setup 造数据 / cleanup 还原

setup：
  1. 给指定 SO（默认 S00124，有 MO+picking 展示最全）打「紧急」tag
  2. 跑 propagate_emergency → 关联 MO priority→1（演示自动继承）
  3. 给 2 条 PO 标 priority=1（演示采购紧急红色显示）
cleanup：全部还原（SO tag 摘除 / MO priority=0 / PO priority=0）

用法（backend/ 目录）：
  python -m scripts.demo_emergency_data setup [SO_NAME]
  python -m scripts.demo_emergency_data cleanup [SO_NAME]
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.services.odoo.client import OdooClient  # noqa: E402
from app.services.odoo.models import (  # noqa: E402
    MODEL_CRM_TAG,
    MODEL_MRP_PRODUCTION,
    MODEL_PURCHASE,
    MODEL_SALE_ORDER,
)
from app.services.sync.emergency_propagation import propagate_emergency  # noqa: E402

DEFAULT_SO = "S00124"
# 演示用的采购单（直接标紧急，展示采购看板红列）
DEMO_POS = ["P00018", "P00021"]


async def _tag_id(client: OdooClient, name: str) -> int | None:
    tags = await client.search_read(MODEL_CRM_TAG, [["name", "=", name]], ["id"])
    return tags[0]["id"] if tags else None


def _has_tag(tag_ids: list, tag_id: int) -> bool:
    """兼容 Odoo 18 m2m 返回纯 id 列表 [1] 或 (id, name) 元组"""
    for t in tag_ids or []:
        if isinstance(t, (list, tuple)):
            if t and t[0] == tag_id:
                return True
        elif t == tag_id:
            return True
    return False


async def setup(client: OdooClient, so_name: str) -> int:
    print(f"[setup] 目标 SO={so_name}")

    tag = await _tag_id(client, "紧急")
    if not tag:
        print("[错误] 紧急 tag 未建，先跑 python -m scripts.init_crm_tags")
        return 1

    sos = await client.search_read(MODEL_SALE_ORDER, [["name", "=", so_name]],
                                   ["id", "name", "state", "tag_ids"])
    if not sos:
        print(f"[错误] SO {so_name} 不存在")
        return 1
    so = sos[0]

    # 1. SO 打紧急 tag
    if not _has_tag(so["tag_ids"], tag):
        await client.write(MODEL_SALE_ORDER, [so["id"]], {"tag_ids": [(4, tag)]})
        print(f"  ✔ {so_name} 已打「紧急」tag")
    else:
        print(f"  · {so_name} 已有「紧急」tag")

    # 2. 继承：MO priority→1
    result = await propagate_emergency(client)
    print(f"  ✔ 紧急继承 sync: 关联销售={result['emergency_sales']}, "
          f"PO 影响 {result['affected_po']}, MO 影响 {result['affected_mo']}")

    # 3. PO 直接标紧急（演示采购看板红列 + 紧急泳道右列）
    pos = await client.search_read(MODEL_PURCHASE, [["name", "in", DEMO_POS]],
                                   ["id", "name", "priority"])
    for p in pos:
        if (p.get("priority") or "") != "1":
            await client.write(MODEL_PURCHASE, [p["id"]], {"priority": "1"})
            print(f"  ✔ {p['name']} priority 0→1")
        else:
            print(f"  · {p['name']} 已是紧急")

    print("\n[setup 完成] 刷新交付塔页面查看：紧急泳道 / 采购看板红列 / 销售表紧急徽章 / 抽屉红色生产单")
    return 0


async def cleanup(client: OdooClient, so_name: str) -> int:
    print(f"[cleanup] 目标 SO={so_name}")

    tag = await _tag_id(client, "紧急")

    # 1. 摘 SO tag
    sos = await client.search_read(MODEL_SALE_ORDER, [["name", "=", so_name]],
                                   ["id", "name", "tag_ids"])
    if sos and tag and _has_tag(sos[0]["tag_ids"], tag):
        await client.write(MODEL_SALE_ORDER, [sos[0]["id"]], {"tag_ids": [(3, tag)]})
        print(f"  ✔ {so_name} 摘除「紧急」tag")
    else:
        print(f"  · {so_name} 无紧急 tag（或不存在）")

    # 2. 该 SO 关联 MO priority 还原 0
    mos = await client.search_read(MODEL_MRP_PRODUCTION, [["origin", "=", so_name]],
                                   ["id", "name", "priority"])
    for m in mos:
        if (m.get("priority") or "") != "0":
            await client.write(MODEL_MRP_PRODUCTION, [m["id"]], {"priority": "0"})
            print(f"  ✔ {m['name']} priority →0")
    if not mos:
        print(f"  · {so_name} 无关联 MO")

    # 3. PO priority 还原 0
    pos = await client.search_read(MODEL_PURCHASE, [["name", "in", DEMO_POS]],
                                   ["id", "name", "priority"])
    for p in pos:
        if (p.get("priority") or "") != "0":
            await client.write(MODEL_PURCHASE, [p["id"]], {"priority": "0"})
            print(f"  ✔ {p['name']} priority →0")

    print("\n[cleanup 完成] 数据已还原")
    return 0


async def main() -> int:
    settings = get_settings()
    client = OdooClient.get_instance(settings)
    if not client.is_configured():
        print("[错误] ODOO_PASSWORD 未配置")
        return 1
    await client.authenticate()

    action = sys.argv[1] if len(sys.argv) > 1 else "setup"
    so_name = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_SO

    if action == "setup":
        return await setup(client, so_name)
    if action == "cleanup":
        return await cleanup(client, so_name)
    print(f"未知动作: {action}（支持 setup / cleanup）")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))