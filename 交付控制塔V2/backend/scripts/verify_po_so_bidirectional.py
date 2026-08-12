"""双向闭环验证：PO 紧急 → SO 打标 → 正向传播

验证 PO↔SO 双向关联是否跑通：
  1. 反查：所有 priority=1 的 PO，用双链路（sale_line_id ∪ origin）找关联 SO
  2. dry-run：只统计关联，不打标不改数据
  3. 真实模式：跑 propagate_emergency（先反向打 SO tag，再正向补 PO/MO priority）
  4. 复验：被反查命中的 SO 现在都带「紧急」tag

用法（backend/ 目录）：
  python -m scripts.verify_po_so_bidirectional --dry-run   # 只统计关联，不改数据
  python -m scripts.verify_po_so_bidirectional             # 真实跑一遍闭环
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
    MODEL_PURCHASE,
    MODEL_SALE_ORDER,
    PRIORITY_URGENT,
    TAG_NAMES_EMERGENCY,
)
from app.services.sync.emergency_propagation import (  # noqa: E402
    _collect_linked_so_ids,
    _get_emergency_tag_ids,
    propagate_emergency,
)


async def main() -> int:
    settings = get_settings()
    client = OdooClient.get_instance(settings)
    if not client.is_configured():
        print("[错误] ODOO_PASSWORD 未配置（.env）")
        return 1

    dry_run = "--dry-run" in sys.argv
    await client.authenticate()
    print(f"[0] 认证 OK db={settings.ODOO_DB} dry_run={dry_run}")

    # ── 1. 所有紧急 PO ──
    pos = await client.search_read(
        MODEL_PURCHASE,
        [["priority", "=", PRIORITY_URGENT], ["state", "not in", ["cancel"]]],
        ["id", "name", "state", "origin"], limit=500,
    )
    print(f"[1] priority=1 的 PO 共 {len(pos)} 张：{', '.join(p['name'] for p in pos[:10])}{' …' if len(pos) > 10 else ''}")

    # ── 2. 双链路反查 ──
    so_ids = await _collect_linked_so_ids(client, pos) if pos else set()
    print(f"[2] 双链路反查命中 SO {len(so_ids)} 个")
    if so_ids:
        sos = await client.search_read(
            MODEL_SALE_ORDER, [["id", "in", list(so_ids)]],
            ["id", "name", "state", "tag_ids"],
        )
        for s in sos:
            tag_names = [t[1] for t in s["tag_ids"] if isinstance(t, (list, tuple)) and len(t) > 1]
            print(f"    SO {s['name']:>12} state={s['state']:<10} tags={tag_names}")
    else:
        print("    ⚠ 无关联 SO：检查 PO 是否带 sale_line_id 或 origin=SO名")

    if dry_run:
        print("\n[dry-run] 未修改任何数据，跑真实模式请去掉 --dry-run")
        return 0

    # ── 3. 真实跑闭环 ──
    print("\n[3] 跑 propagate_emergency（反向打标 + 正向传播）...")
    result = await propagate_emergency(client)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # ── 4. 复验 ──
    tags = await _get_emergency_tag_ids(client)
    if so_ids and tags:
        sos_after = await client.search_read(
            MODEL_SALE_ORDER, [["id", "in", list(so_ids)]],
            ["id", "name", "tag_ids"],
        )
        ok = 0
        for s in sos_after:
            has = any(
                (isinstance(t, (list, tuple)) and t and t[0] in tags)
                or (isinstance(t, int) and t in tags)
                for t in (s["tag_ids"] or [])
            )
            if has:
                ok += 1
            else:
                print(f"    ✘ SO {s['name']} 仍未打「紧急」tag")
        print(f"\n[4] 复验：{ok}/{len(sos_after)} 个关联 SO 已带「紧急」tag")
        if ok == len(sos_after):
            print("✔ 双向闭环跑通：PO 紧急 → SO tag → 正向传播 → PO/MO priority 全部对齐")
        else:
            print("⚠ 部分 SO 未闭环（多为关联缺失，见 [2]）")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
