from __future__ import annotations

import http.cookiejar
import json
import urllib.error
import urllib.request
from typing import Any

from .config import settings


class OdooError(RuntimeError):
    pass


class OdooClient:
    def __init__(self) -> None:
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.authenticated = False

    def _rpc(self, path: str, params: dict[str, Any]) -> Any:
        body = json.dumps({"jsonrpc": "2.0", "method": "call", "params": params, "id": 1}).encode()
        request = urllib.request.Request(f"{settings.odoo_base_url}{path}", data=body, headers={"Content-Type": "application/json", "User-Agent": "constellation-platform/1.0"})
        try:
            with self.opener.open(request, timeout=settings.odoo_timeout_seconds) as response:
                payload = json.load(response)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise OdooError("Odoo 暂时无法连接") from error
        if payload.get("error"):
            message = payload["error"].get("data", {}).get("message") or payload["error"].get("message")
            raise OdooError(f"Odoo 拒绝请求：{message or '未知错误'}")
        return payload.get("result")

    def authenticate(self) -> None:
        if not settings.odoo_ready:
            raise OdooError("Odoo 连接参数尚未配置")
        result = self._rpc("/web/session/authenticate", {"db": settings.odoo_database, "login": settings.odoo_username, "password": settings.odoo_api_key})
        if not isinstance(result, dict) or not result.get("uid"):
            raise OdooError("Odoo 集成账号认证失败")
        self.authenticated = True

    def search_read(self, model: str, domain: list, fields: list[str], *, limit: int = 20, order: str = "id asc") -> list[dict[str, Any]]:
        if not self.authenticated:
            self.authenticate()
        result = self._rpc(f"/web/dataset/call_kw/{model}/search_read", {"model": model, "method": "search_read", "args": [domain], "kwargs": {"fields": fields, "limit": limit, "order": order}})
        return result if isinstance(result, list) else []

    def customers(self, limit: int = 20, since: str = "") -> list[dict[str, Any]]:
        domain: list = [["active", "=", True], ["customer_rank", ">", 0]]
        if since:
            domain.append(["write_date", ">", since])
        return self.search_read(
            "res.partner",
            domain,
            ["name", "ref", "phone", "mobile", "email", "street", "street2", "city", "state_id", "country_id", "user_id", "write_date", "is_company", "parent_id"],
            limit=max(1, min(limit, 200)),
            order="write_date asc,id asc",
        )


odoo_client = OdooClient()

