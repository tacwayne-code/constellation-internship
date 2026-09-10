"""企业微信小程序统一身份网关。

只接收 wx.login 的一次性 code；AppSecret 永远只从服务器环境读取。
未知微信帐号登记为待授权状态，管理员用 identityctl.py 分配业务角色后才会签发短时票据。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def b64_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def sign_ticket(payload: dict[str, Any], secret: str) -> str:
    encoded = b64_encode(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode())
    signature = b64_encode(hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest())
    return f"v1.{encoded}.{signature}"


class IdentityStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS identities (
                    subject TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL DEFAULT '待授权员工',
                    crm_role TEXT NOT NULL DEFAULT '',
                    service_role TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )"""
            )

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def record_login(self, subject: str) -> dict[str, Any]:
        now = int(time.time())
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO identities(subject, created_at, updated_at) VALUES (?, ?, ?)",
                (subject, now, now),
            )
            db.execute("UPDATE identities SET updated_at = ? WHERE subject = ?", (now, subject))
            row = db.execute("SELECT * FROM identities WHERE subject = ?", (subject,)).fetchone()
        return dict(row)


class App:
    def __init__(self) -> None:
        self.app_id = os.environ.get("WECHAT_APP_ID", "").strip()
        self.app_secret = os.environ.get("WECHAT_APP_SECRET", "").strip()
        self.ticket_secret = os.environ.get("SSO_SHARED_SECRET", "").strip()
        if not all((self.app_id, self.app_secret, self.ticket_secret)):
            raise RuntimeError("WECHAT_APP_ID、WECHAT_APP_SECRET、SSO_SHARED_SECRET 必须配置")
        self.store = IdentityStore(Path(os.environ.get("IDENTITY_DB", "identity.db")))

    def exchange_code(self, code: str) -> str:
        if not code:
            raise ValueError("缺少微信登录凭证")
        query = urllib.parse.urlencode({"appid": self.app_id, "secret": self.app_secret, "js_code": code, "grant_type": "authorization_code"})
        try:
            with urllib.request.urlopen(f"https://api.weixin.qq.com/sns/jscode2session?{query}", timeout=8) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as error:
            raise RuntimeError("微信身份服务暂时不可用") from error
        if payload.get("errcode") or not payload.get("openid"):
            raise RuntimeError("微信身份校验失败")
        return str(payload["openid"])

    def login(self, code: str) -> dict[str, Any]:
        subject = self.exchange_code(code)
        identity = self.store.record_login(subject)
        if identity["status"] != "ACTIVE":
            registration_code = hashlib.sha256(subject.encode()).hexdigest()[:16]
            return {"status": "PENDING", "registrationCode": registration_code,
                    "message": "身份已登记，等待管理员授权。登记码：" + registration_code}
        now = int(time.time())
        base = {"sub": subject, "name": identity["display_name"], "iat": now, "exp": now + 120, "jti": secrets.token_urlsafe(18)}
        tickets: dict[str, str] = {}
        roles: list[str] = []
        if identity["crm_role"]:
            tickets["crm"] = sign_ticket({**base, "aud": "crm", "role": identity["crm_role"]}, self.ticket_secret)
            roles.append("sales_manager" if identity["crm_role"] == "销售经理" else "sales")
        if identity["service_role"]:
            tickets["after_sales"] = sign_ticket({**base, "aud": "service", "role": identity["service_role"]}, self.ticket_secret)
            roles.append(identity["service_role"])
        return {
            "status": "AUTHORIZED",
            "employee": {"id": hashlib.sha256(subject.encode()).hexdigest()[:16], "name": identity["display_name"]},
            "roles": roles,
            "modules": list(tickets),
            "tickets": tickets,
        }


class Handler(BaseHTTPRequestHandler):
    server_version = "IdentityGateway/1.0"

    def _json(self, status: int, body: dict[str, Any]) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json(200, {"status": "ok"})
        else:
            self._json(404, {"message": "接口不存在"})

    def do_POST(self) -> None:
        if self.path != "/auth/wechat/login":
            self._json(404, {"message": "接口不存在"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 16384:
                raise ValueError("请求参数不正确")
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            self._json(200, self.server.application.login(str(body.get("code") or "")))
        except ValueError as error:
            self._json(400, {"message": str(error)})
        except RuntimeError as error:
            self._json(502, {"message": str(error)})
        except Exception:
            self._json(500, {"message": "身份服务暂时不可用"})

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    host = os.environ.get("IDENTITY_HOST", "127.0.0.1")
    port = int(os.environ.get("IDENTITY_PORT", "8010"))
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.application = App()
    httpd.serve_forever()


if __name__ == "__main__":
    main()
