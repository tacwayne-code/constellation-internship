"""端到端集成测试：紧急继承 sync 在真实数据上跑通

流程：
  1. 找一个 SO（默认 S00011，可命令行覆盖）
  2. 记下该 SO 当前 tag + 关联 MO 的 priority（备份）
  3. 给 SO 加「紧急」tag
  4. 跑 propagate_emergency
  5. 验证：MO priority 被改成 3
  6. 回滚：移除 SO 的紧急 tag + MO priority 改回原值

幂等；测试完不会污染数据。

用法：
  python -m scripts.integration_test_emergency_propagation SO_NAME
  python -m scripts.integration_test_emergency_propagation            # 默认 S00011
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.services.odoo.client import OdooClient  # noqa: E402
from app.services.odoo.models import (  # noqa: E402
    MODEL_CRM_TAG,
    MODEL_MRP_PRODUCTION,
    MODEL_SALE_ORDER,
    PRIORITY_URGENT,
)
from app.services.sync.emergency_propagation import propagate_emergency  # noqa: E402


async def main() -> int:
    settings = get_settings()
    client = OdooClient.get_instance(settings)
    if not client.is_configured():
        print("[错误] ODOO_PASSWORD 未配置")
        return 1

    target_so_name = sys.argv[1] if len(sys.argv) > 1 else "S00011"
    print(f"[集成测试] 目标销售订单: {target_so_name}")

    # 0. 找紧急 tag id
    tags = await client.search_read(MODEL_CRM_TAG, [["name", "=", "紧急"]], ["id", "name"])
    if not tags:
        print("[错误] 紧急 tag 不存在，请先 python -m scripts.init_crm_tags")
        return 1
    tag_id = tags[0]["id"]
    print(f"[0] 紧急 tag id={tag_id}")

    # 1. 找目标 SO
    sos = await client.search_read(MODEL_SALE_ORDER, [["name", "=", target_so_name]],
                                   ["id", "name", "state", "tag_ids"])
    if not sos:
        print(f"[错误] 销售订单 {target_so_name} 不存在")
        return 1
    so = sos[0]
    print(f"[1] 找到 SO id={so['id']} state={so['state']} 当前 tag={[t[1] for t in so['tag_ids']]}")

    # 2. 备份当前 MO priority（origin 匹配）
    mos_before = await client.search_read(
        MODEL_MRP_PRODUCTION, [["origin", "=", target_so_name]],
        ["id", "name", "origin", "priority"],
    )
    backup = {m["id"]: m.get("priority") or 0 for m in mos_before}
    print(f"[2] 关联 MO 共 {len(mos_before)} 条，priority 备份: {backup}")

    try:
        # 3. 给 SO 加紧急 tag
        if not any(t[0] == tag_id for t in so["tag_ids"]):
            await client.write(MODEL_SALE_ORDER, [so["id"]],
                               {"tag_ids": [(4, tag_id)]})
            print(f"[3] 已给 {target_so_name} 加「紧急」tag")
        else:
            print(f"[3] {target_so_name} 已有「紧急」tag，跳过")

        # 4. 跑 sync
        result = await propagate_emergency(client)
        print(f"[4] sync 结果: {json.dumps(result, ensure_ascii=False, indent=2)}")

        # 5. 验证：MO priority 变成 3
        mos_after = await client.search_read(
            MODEL_MRP_PRODUCTION, [["origin", "=", target_so_name]],
            ["id", "name", "priority"],
        )
        all_urgent = all((m.get("priority") or 0) == PRIORITY_URGENT for m in mos_after)
        print(f"[5] MO 当前 priority: {[(m['name'], m.get('priority')) for m in mos_after]}")
        print(f"    全部紧急={PRIORITY_URGENT}? {all_urgent}")

        if all_urgent and mos_after:
            print("\n✔ 集成测试通过：紧急标签 → MO priority=3 链路 OK")
        elif not mos_after:
            print("\n⚠ 无关联 MO（origin 匹配为空），只验证了 SO 识别链路")
        else:
            print("\n✘ 部分 MO 未变更紧急")
            return 2

    finally:
        # 6. 回滚（无论成败）
        print("\n[6] 回滚中...")
        # 移除紧急 tag
        await client.write(MODEL_SALE_ORDER, [so["id"]],
                           {"tag_ids": [(3, tag_id)]})
        print(f"    已移除 {target_so_name} 的「紧急」tag")
        # MO priority 还原
        if backup:
            for mid, original_p in backup.items():
                await client.write(MODEL_MRP_PRODUCTION, [mid], {"priority": original_p})
            print(f"    已还原 {len(backup)} 个 MO priority → {set(backup.values())}")
        print("[6] 回滚完毕")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))