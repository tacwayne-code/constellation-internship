"""Odoo 数据全面摸底：梳理各模型数据归属板块"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.services.odoo.client import OdooClient

# (模型, 归属板块, 说明)
CANDIDATES = [
    ("project.project", "overview", "项目"),
    ("project.task", "delivery", "任务/交付包"),
    ("project.milestone", "delivery", "里程碑"),
    ("project.update", "overview", "项目更新"),
    ("purchase.order", "procurement", "采购单"),
    ("purchase.order.line", "procurement", "采购明细"),
    ("stock.picking", "logistics", "库存流转"),
    ("stock.quant", "inventory", "库存量"),
    ("stock.move", "logistics", "库存移动"),
    ("res.partner", "vendors", "伙伴/供应商"),
    ("hr.employee", "people", "员工"),
    ("hr.attendance", "people", "考勤"),
    ("hr.department", "people", "部门"),
    ("mrp.production", "electrical", "生产工单"),
    ("quality.check", "commissioning", "质量检查"),
    ("documents.document", "documents", "文档"),
    ("account.move", "overview", "会计凭证"),
    ("product.product", "inventory", "产品"),
    ("product.template", "inventory", "产品模板"),
    ("sale.order", "overview", "销售订单"),
    ("maintenance.request", "electrical", "维护请求"),
    ("maintenance.equipment", "electrical", "设备"),
    ("fleet.vehicle", "logistics", "车辆"),
]


async def main():
    client = OdooClient.get_instance(get_settings())
    print("=" * 60)
    print("Odoo 模型数据摸底（inspiri_erp_test）")
    print("=" * 60)

    # 1. 各候选模型计数
    results = []
    for model, board, desc in CANDIDATES:
        try:
            count = await client.search_count(model, [])
            results.append((model, board, desc, count))
        except Exception:
            results.append((model, board, desc, -1))  # 模型不存在/无权限

    print(f"\n{'模型':<28}{'板块':<14}{'说明':<14}{'数量'}")
    print("-" * 70)
    for model, board, desc, count in sorted(results, key=lambda x: -x[3]):
        cnt = "不可用" if count < 0 else str(count)
        print(f"{model:<28}{board:<14}{desc:<14}{cnt}")

    # 2. project.task 按项目 + 阶段分组
    print("\n" + "=" * 60)
    print("project.task 按项目分组")
    print("=" * 60)
    tasks = await client.search_read("project.task", [], ["project_id", "state", "stage_id", "name"], limit=2000)
    from collections import Counter, defaultdict
    by_proj = defaultdict(Counter)
    for t in tasks:
        pid = t.get("project_id")
        pname = pid[1] if isinstance(pid, (list, tuple)) and len(pid) > 1 else str(pid)
        state = t.get("state") or "?"
        by_proj[pname][state] += 1
    for pname, states in sorted(by_proj.items()):
        print(f"  {pname}: {dict(states)}")

    # 3. 已安装模块列表（前 40）
    print("\n" + "=" * 60)
    print("已安装模块（前 40）")
    print("=" * 60)
    try:
        mods = await client.search_read(
            "ir.module.module",
            [("state", "=", "installed")],
            ["name", "shortdesc"],
            limit=40,
        )
        for m in mods:
            print(f"  {m['name']:<32}{m.get('shortdesc') or ''}")
    except Exception as e:
        print(f"  读取失败: {e}")


asyncio.run(main())
