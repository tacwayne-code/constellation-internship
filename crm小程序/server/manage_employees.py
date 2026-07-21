#!/usr/bin/env python3
"""Manage the server-side CRM employee whitelist without user passwords."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

try:
    from .wechat_auth import AuthError, EmployeeStore, normalize_phone
except ImportError:  # pragma: no cover - direct script execution
    from wechat_auth import AuthError, EmployeeStore, normalize_phone


def mask_phone(phone: str) -> str:
    return f"{phone[:3]}****{phone[-4:]}" if len(phone) == 11 else phone


def main() -> None:
    parser = argparse.ArgumentParser(description="CRM员工白名单管理")
    parser.add_argument(
        "--file",
        type=Path,
        default=Path(os.environ.get("CRM_EMPLOYEE_FILE", "server/data/employees.json")),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("list", help="查看员工名单")
    upsert = commands.add_parser("set", help="新增或更新员工")
    upsert.add_argument("--id", dest="employee_id", help="可省略，默认使用手机号")
    upsert.add_argument("--name", required=True)
    upsert.add_argument("--phone", required=True)
    upsert.add_argument("--role", choices=["销售人员", "销售经理"], default="销售人员")

    disable = commands.add_parser("disable", help="停用员工")
    disable.add_argument("--id", required=True, dest="employee_id")

    unbind = commands.add_parser("unbind", help="解除员工微信绑定")
    unbind.add_argument("--id", required=True, dest="employee_id")

    args = parser.parse_args()
    store = EmployeeStore(args.file)
    try:
        if args.command == "list":
            rows = store.list()
            if not rows:
                print("员工名单为空")
            for row in rows:
                status = "启用" if row.get("active", True) else "停用"
                bound = "已绑定微信" if row.get("openid") else "未绑定微信"
                print(
                    f"{row.get('id')}  {row.get('name')}  "
                    f"{mask_phone(str(row.get('phone') or ''))}  "
                    f"{row.get('role')}  {status}  {bound}"
                )
            return
        if args.command == "set":
            employee_id = args.employee_id or normalize_phone(args.phone)
            saved = store.upsert(
                {
                    "id": employee_id,
                    "name": args.name,
                    "phone": args.phone,
                    "role": args.role,
                    "active": True,
                }
            )
        else:
            current = store.find_id(args.employee_id)
            if not current:
                raise AuthError("未找到该员工", 404, "EMPLOYEE_NOT_FOUND")
            if args.command == "disable":
                current["active"] = False
            elif args.command == "unbind":
                current["openid"] = ""
            saved = store.upsert(current)
        print(f"已更新：{saved['id']} {saved['name']}")
    except AuthError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
