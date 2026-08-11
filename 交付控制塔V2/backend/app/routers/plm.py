"""PLM 路由：连接状态与文档数据"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response

from app.config import get_settings, Settings
from app.services.plm.registry import get_plm_adapter

router = APIRouter(prefix="/api", tags=["plm"])


def _settings() -> Settings:
    return get_settings()


@router.get("/plm/health")
async def plm_health():
    adapter = get_plm_adapter(_settings())
    status = await adapter.health()
    return {
        "adapter": adapter.name,
        "url": _settings().PLM_URL or "",
        **status,
    }


@router.get("/plm/documents")
async def plm_documents(resp: Response, project_key: str | None = None, limit: int = Query(100, ge=1, le=500)):
    adapter = get_plm_adapter(_settings())
    docs = await adapter.list_documents(project_key=project_key)
    if not docs:
        resp.headers["X-Data-Source"] = "mock"
    return docs[:limit]
