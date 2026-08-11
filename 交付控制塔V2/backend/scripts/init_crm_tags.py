"""一次性初始化 crm.tag 紧急标签（不改 Odoo 模块，仅 create 数据）

建三个标签用于销售订单紧急/非标/普通标记：
  - 紧急   color=1 (红)   用于需要紧急交付的订单
  - 非标订单 color=3 (黄)  用于非标订单
  - 普通   color=0 (灰)   默认普通订单

幂等：标签已存在则跳过。

用法（backend/ 目录）：
    python -m scripts.init_crm_tags
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.services.odoo.client import OdooClient  # noqa: E402


# 待创建的标签：name → color 索引（Odoo crm.tag.color: 1红 3黄）
# 注：需求图明确「普通=黄色」与「紧急=红色」
TAGS_TO_CREATE = [
    {"name": "紧急",   "color": 1},
    {"name": "非标订单", "color": 3},
    {"name": "普通",   "color": 3},
]


async def main() -> int:
    settings = get_settings()
    client = OdooClient.get_instance(settings)

    if not client.is_configured():
        print("[错误] ODOO_PASSWORD 未配置")
        return 1

    # 认证
    uid = await client.authenticate()
    print(f"[1] 认证 uid={uid} (db={settings.ODOO_DB})")

    # 查已存在标签
    existing = await client.search_read(
        "crm.tag", [["name", "in", [t["name"] for t in TAGS_TO_CREATE]]],
        ["id", "name", "color"],
    )
    existing_names = {t["name"] for t in existing}
    print(f"[2] crm.tag 已存在: {existing_names or '（无）'}")

    created = []
    updated = []
    skipped = []
    for spec in TAGS_TO_CREATE:
        existing_match = next((t for t in existing if t["name"] == spec["name"]), None)
        if existing_match:
            # 已存在：核对 color，不一致则 update（兼容历史错误）
            if existing_match.get("color") != spec["color"]:
                await client.write("crm.tag", [existing_match["id"]],
                                   {"color": spec["color"]})
                updated.append({"id": existing_match["id"], **spec})
                print(f"    更新: id={existing_match['id']} {spec['name']} color {existing_match.get('color')}→{spec['color']}")
            else:
                skipped.append(spec["name"])
            continue
        new_id = await client.create("crm.tag", spec)
        created.append({"id": new_id, **spec})
        print(f"    创建: id={new_id} {spec['name']} color={spec['color']}")

    # 输出最终标签映射
    final = await client.search_read(
        "crm.tag", [["name", "in", [t["name"] for t in TAGS_TO_CREATE]]],
        ["id", "name", "color"], order="id",
    )
    print("\n[结果] crm.tag 紧急标签集：")
    for t in final:
        print(f"  id={t['id']:>4}  name={t['name']:<6}  color={t['color']}")
    print(f"\n✔ 本次新建 {len(created)} 个，更新 {len(updated)} 个，跳过 {len(skipped)} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))