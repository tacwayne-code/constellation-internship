"""适配器基类 + 数据源统一调度（Odoo → Mock 降级）"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any

from app.services.odoo.client import OdooClient

logger = logging.getLogger(__name__)


class BaseRowAdapter(ABC):
    """将 Odoo 标准模型记录转换为前端 S() 行契约"""

    #: Odoo 模型名（子类覆盖）
    model: str = ""
    #: 读取字段（子类覆盖）
    fields: list[str] = []

    @abstractmethod
    def to_row(self, record: dict, project_id: str | None = None) -> dict:
        """返回 {id, name, cells, status, tone, fields, progress}"""

    def fields_meta(self, record: dict) -> list[list[str]]:
        """详情字段 [[label, value], ...]"""
        return []


def _fmt_owner(user_id) -> str:
    """user_id 可能是 [id, name] 或 id"""
    if isinstance(user_id, (list, tuple)) and len(user_id) > 1:
        return str(user_id[1])
    if isinstance(user_id, (list, tuple)) and len(user_id) == 1:
        return str(user_id[0])
    if isinstance(user_id, int):
        return f"用户#{user_id}"
    return str(user_id or "—")


def _fmt_partner(partner_id) -> str:
    """partner_id 可能是 [id, name]"""
    if isinstance(partner_id, (list, tuple)) and len(partner_id) > 1:
        return str(partner_id[1])
    return "—"


async def fetch_with_fallback(
    client: OdooClient,
    adapter: BaseRowAdapter,
    domain: list | None = None,
    mock_key: str | None = None,
    limit: int | None = None,
    project_id: str | None = None,
    force_mock: bool = False,
) -> tuple[list[dict], str]:
    """数据源调度：Odoo 优先（失败自动重认证 + 指数退避），未配置/失败 → Mock 降级"""
    if force_mock or not client.is_configured():
        rows = await fetch_mock_rows(mock_key) if mock_key else []
        return rows, "mock"

    last_err: Exception | None = None
    for attempt in range(3):
        try:
            records = await client.search_read(
                adapter.model, domain or [], adapter.fields, limit=limit
            )
            if records:
                # 收集 user_ids（task 模型的 user_ids 是 m2m，需解析 name）
                if adapter.model == "project.task":
                    uids: set[int] = set()
                    for r in records:
                        for uid in r.get("user_ids") or []:
                            if isinstance(uid, int):
                                uids.add(uid)
                    if uids:
                        user_map = await client.get_user_names(list(uids))
                        for r in records:
                            ids = [uid for uid in (r.get("user_ids") or []) if isinstance(uid, int)]
                            r["_user_names"] = [
                                {"id": uid, "name": user_map.get(uid, f"用户#{uid}")}
                                for uid in ids
                            ]
                return [adapter.to_row(r, project_id) for r in records], "odoo"
            # 空结果：再试一次（避免瞬时空）
            if attempt == 0:
                continue
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning("Odoo %s 第 %d 次读取失败: %s", adapter.model, attempt + 1, e)
            if attempt < 2:
                try:
                    await client.authenticate()  # 重认证
                except Exception:
                    pass
                await asyncio.sleep(0.2 * (2 ** attempt))  # 0.2s / 0.4s 退避

    if last_err:
        logger.warning("Odoo %s 全部重试失败，降级 Mock[%s]: %s", adapter.model, mock_key, last_err)
    rows = await fetch_mock_rows(mock_key) if mock_key else []
    return rows, "mock"


async def fetch_mock_rows(mock_key: str) -> list[dict]:
    """直接从 Mock JSON 读取行数据（透传，已符合 S() 契约）"""
    from app.services.mock_data import mock_rows, load_mock

    if mock_key in ("projects", "risks"):
        return load_mock(mock_key)
    return mock_rows(mock_key)
