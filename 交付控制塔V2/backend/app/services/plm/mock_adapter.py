"""Mock PLM 适配器：未配置时的占位实现"""
from __future__ import annotations

from app.config import Settings


class MockPlmAdapter:
    """PLM 未配置/不可达时的占位：返回空数据并明确标记"""

    name = "mock"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def health(self) -> dict:
        configured = bool(self.settings.PLM_URL)
        return {
            "ok": False,
            "configured": configured,
            "detail": "not_configured" if not configured else "mock_mode",
        }

    async def list_documents(self, project_key: str | None = None) -> list[dict]:
        return []

    async def get_document(self, doc_id: str) -> dict | None:
        return None
