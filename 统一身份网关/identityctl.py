"""仅供服务器管理员使用的身份授权工具；不通过公网暴露管理接口。"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from app import IdentityStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["list", "grant", "disable"])
    parser.add_argument("--subject")
    parser.add_argument("--name")
    parser.add_argument("--crm-role", choices=["销售人员", "销售经理"], default="")
    parser.add_argument("--service-role", choices=["paidan", "engineer"], default="")
    args = parser.parse_args()
    store = IdentityStore(Path(os.environ.get("IDENTITY_DB", "identity.db")))
    with store.connect() as db:
        if args.command == "list":
            for row in db.execute("SELECT subject, display_name, crm_role, service_role, status, updated_at FROM identities ORDER BY updated_at DESC"):
                print(" | ".join(str(row[key]) for key in row.keys()))
            return
        if not args.subject:
            parser.error("--subject 是必填项")
        if args.command == "disable":
            db.execute("UPDATE identities SET status='DISABLED' WHERE subject=?", (args.subject,))
        else:
            if not args.name:
                parser.error("grant 需要 --name")
            db.execute("UPDATE identities SET display_name=?, crm_role=?, service_role=?, status='ACTIVE' WHERE subject=?", (args.name, args.crm_role, args.service_role, args.subject))
        db.commit()


if __name__ == "__main__":
    main()
