"""Odoo 18 连通性测试脚本

用法（在 backend/ 目录下）：
    python -m scripts.test_odoo_connectivity

输出：Odoo 版本、认证 uid、project.project 前 3 条记录摘要
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 允许从 backend/ 直接运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.services.odoo.client import OdooClient  # noqa: E402


async def main() -> int:
    settings = get_settings()
    client = OdooClient.get_instance(settings)

    print(f"Odoo URL     : {settings.ODOO_URL}")
    print(f"数据库        : {settings.ODOO_DB}")
    print(f"用户          : {settings.ODOO_USER}")
    print(f"已配置凭据     : {client.is_configured()}")

    if not client.is_configured():
        print("\n[错误] ODOO_PASSWORD 未配置。请复制 .env.example 为 .env 并填写密码。")
        return 1

    print("\n[1/3] 获取服务器版本 ...")
    import xmlrpc.client

    common = xmlrpc.client.ServerProxy(f"{settings.ODOO_URL}/xmlrpc/2/common")
    version = common.version()
    print(f"       server_version : {version.get('server_version')}")
    print(f"       server_serie   : {version.get('server_serie')}")

    print("\n[2/3] 认证 ...")
    uid = await client.authenticate()
    print(f"       认证成功，uid = {uid}")

    print("\n[3/3] 读取 project.project 前 3 条 ...")
    projects = await client.search_read(
        "project.project",
        [],
        ["id", "name", "user_id"],
        limit=3,
    )
    if not projects:
        print("       （当前无项目数据）")
    for p in projects:
        print(f"       - [{p['id']}] {p['name']} (负责人 {p.get('user_id')})")

    print("\n✔ Odoo 连通性验证通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
