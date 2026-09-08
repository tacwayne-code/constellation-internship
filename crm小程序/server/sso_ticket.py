"""验证由统一身份网关签发的短时登录票据。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from wechat_auth import AuthError


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def verify_gateway_ticket(ticket: str, secret: str, audience: str) -> dict[str, Any]:
    try:
        version, encoded, signature = str(ticket or "").split(".")
        expected = base64.urlsafe_b64encode(
            hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest()
        ).rstrip(b"=").decode()
        if version != "v1" or not secret or not hmac.compare_digest(signature, expected):
            raise ValueError
        payload = json.loads(_decode(encoded).decode())
        if payload.get("aud") != audience or int(payload.get("exp") or 0) < int(time.time()):
            raise ValueError
        if not all(isinstance(payload.get(key), str) and payload[key] for key in ("sub", "name", "role")):
            raise ValueError
        return payload
    except Exception as error:
        raise AuthError("登录链接已失效，请返回小程序重试", 401, "TICKET_INVALID") from error
