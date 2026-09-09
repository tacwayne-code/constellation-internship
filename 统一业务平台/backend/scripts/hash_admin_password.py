from __future__ import annotations

import getpass
import hashlib
import secrets


def main() -> None:
    password = getpass.getpass("管理员密码：")
    confirm = getpass.getpass("再次输入：")
    if len(password) < 14:
        raise SystemExit("密码至少需要 14 个字符")
    if password != confirm:
        raise SystemExit("两次输入不一致")
    rounds = 600_000
    salt = secrets.token_bytes(16)
    value = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds).hex()
    print(f"pbkdf2_sha256${rounds}${salt.hex()}${value}")


if __name__ == "__main__":
    main()
