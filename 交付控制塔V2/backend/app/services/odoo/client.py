"""Odoo XML-RPC 客户端封装（认证会话 + 重试 + 线程安全）"""
from __future__ import annotations

import asyncio
import threading
import time
import xmlrpc.client
from typing import Any

from app.config import Settings


class OdooClient:
    """Odoo 18 external API 客户端（xmlrpc.client 标准库实现）。

    特性：
    - 认证 uid 缓存 + TTL 刷新（SESSION_TTL）
    - execute_kw 统一入口，认证失效自动重连重试一次
    - asyncio.Lock 保护认证，同步调用经 asyncio.to_thread 执行
    """

    _instance: "OdooClient | None" = None

    def __init__(self, settings: Settings):
        self.url = settings.ODOO_URL.rstrip("/")
        self.db = settings.ODOO_DB
        self.username = settings.ODOO_USER
        self.password = settings.ODOO_PASSWORD
        self.session_ttl = settings.SESSION_TTL

        self._common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common")
        self._models: xmlrpc.client.ServerProxy | None = None
        self._uid: int | None = None
        self._last_auth: float = 0.0
        self._lock = asyncio.Lock()
        # 线程锁：保护 _models（同一 HTTP 连接）多线程并发安全
        self._thread_lock = threading.Lock()

    @classmethod
    def get_instance(cls, settings: Settings | None = None) -> "OdooClient":
        if cls._instance is None:
            if settings is None:
                from app.config import get_settings

                settings = get_settings()
            cls._instance = cls(settings)
        return cls._instance

    # ---------- 认证 ----------

    def _authenticate_sync(self) -> int:
        """同步认证（供 to_thread 调用）"""
        uid = self._common.authenticate(self.db, self.username, self.password, {})
        if not uid:
            raise PermissionError(
                f"Odoo 认证失败: db={self.db} user={self.username}"
            )
        self._uid = uid
        self._models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object")
        self._last_auth = time.time()
        return uid

    async def authenticate(self) -> int:
        """异步认证，带 TTL 缓存"""
        if self._uid and (time.time() - self._last_auth) < self.session_ttl:
            return self._uid
        async with self._lock:
            # 双检：等待锁期间可能已被刷新
            if self._uid and (time.time() - self._last_auth) < self.session_ttl:
                return self._uid
            return await asyncio.to_thread(self._authenticate_sync)

    def is_configured(self) -> bool:
        return bool(self.username and self.password)

    # ---------- 执行 ----------

    def _execute_sync(self, model: str, method: str, args: list, kwargs: dict) -> Any:
        """同步执行 XML-RPC 调用（在线程锁保护下访问 _models）"""
        assert self._models is not None, "未认证"
        with self._thread_lock:  # 同一时间只允许一个线程调用 _models
            return self._models.execute_kw(
                self.db, self._uid, self.password, model, method, args, kwargs
            )

    async def execute_kw(
        self, model: str, method: str, args: list | None = None, kwargs: dict | None = None
    ) -> Any:
        """统一执行入口；认证失效时重连重试一次"""
        await self.authenticate()
        args = args or []
        kwargs = kwargs or {}
        for attempt in range(2):
            try:
                return await asyncio.to_thread(self._execute_sync, model, method, args, kwargs)
            except xmlrpc.client.Fault as e:
                msg = str(e)
                if attempt == 0 and (
                    "SessionExpired" in msg
                    or "AccessDenied" in msg
                    or "InvalidSession" in msg
                ):
                    self._uid = None
                    await self.authenticate()
                    continue
                raise
            except (ConnectionError, TimeoutError, OSError) as e:
                raise ConnectionError(f"Odoo 连接失败: {e}") from e

    # ---------- 便捷方法 ----------

    async def search_read(
        self,
        model: str,
        domain: list | None = None,
        fields: list[str] | None = None,
        limit: int | None = None,
        offset: int = 0,
        order: str | None = None,
    ) -> list[dict]:
        kwargs: dict[str, Any] = {}
        if limit is not None:
            kwargs["limit"] = limit
        if offset:
            kwargs["offset"] = offset
        if order:
            kwargs["order"] = order
        result = await self.execute_kw(
            model, "search_read", [domain or [], fields or []], kwargs
        )
        return result or []

    async def search_count(self, model: str, domain: list | None = None) -> int:
        return await self.execute_kw(model, "search_count", [domain or []]) or 0

    async def read(self, model: str, ids: list[int], fields: list[str]) -> list[dict]:
        return await self.execute_kw(model, "read", [ids, fields])

    async def create(self, model: str, values: dict) -> int:
        return await self.execute_kw(model, "create", [values])

    async def write(self, model: str, ids: list[int], values: dict) -> bool:
        return await self.execute_kw(model, "write", [ids, values])

    # ---------- 健康检查 ----------

    async def health(self) -> dict:
        """返回 Odoo 连接状态信息"""
        try:
            version = await asyncio.to_thread(
                lambda: self._common.version()
            )
            uid = await self.authenticate()
            return {
                "ok": True,
                "server_version": version.get("server_version"),
                "server_serie": version.get("server_serie"),
                "db": self.db,
                "uid": uid,
                "user": self.username,
            }
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    async def get_user_names(self, user_ids: list[int]) -> dict[int, str]:
        """批量读取 res.users 名称，返回 {uid: name} 映射（自动去重 + 缓存）"""
        if not user_ids:
            return {}
        unique = list({uid for uid in user_ids if isinstance(uid, int) and uid > 0})
        if not unique:
            return {}
        try:
            records = await self.search_read(
                "res.users", [("id", "in", unique)], ["id", "name"]
            )
            return {r["id"]: r["name"] for r in records}
        except Exception:  # noqa: BLE001
            return {}
