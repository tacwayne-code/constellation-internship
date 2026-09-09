from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from fastapi import HTTPException, Request, status

from .config import settings


COOKIE_NAME = "constellation_admin"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def sign_payload(payload: dict[str, Any], secret: str) -> str:
    encoded = _b64(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode())
    signature = _b64(hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest())
    return f"v1.{encoded}.{signature}"


def verify_payload(token: str, secret: str, audience: str | None = None) -> dict[str, Any]:
    try:
        version, encoded, signature = token.split(".", 2)
        expected = _b64(hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest())
        if version != "v1" or not hmac.compare_digest(signature, expected):
            raise ValueError
        payload = json.loads(_unb64(encoded))
        if int(payload.get("exp", 0)) < int(time.time()):
            raise ValueError
        if audience and payload.get("aud") != audience:
            raise ValueError
        return payload
    except Exception as error:
        raise HTTPException(status_code=401, detail="登录状态已失效") from error


def authenticate_admin(username: str, password: str) -> bool:
    if not settings.admin_ready or not secrets.compare_digest(username, settings.admin_username):
        return False
    if settings.admin_password_hash:
        try:
            scheme, rounds, salt, expected = settings.admin_password_hash.split("$", 3)
            actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds)).hex()
            return scheme == "pbkdf2_sha256" and secrets.compare_digest(actual, expected)
        except (ValueError, TypeError):
            return False
    return secrets.compare_digest(password, settings.admin_password)


def create_admin_session() -> str:
    now = int(time.time())
    return sign_payload({"sub": settings.admin_username, "aud": "admin", "iat": now, "exp": now + 8 * 3600}, settings.admin_session_secret)


def require_admin(request: Request) -> str:
    if not settings.admin_ready:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="管理后台尚未配置管理员密钥")
    token = request.cookies.get(COOKIE_NAME, "")
    payload = verify_payload(token, settings.admin_session_secret, "admin")
    return str(payload["sub"])
