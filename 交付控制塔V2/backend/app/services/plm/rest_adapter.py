"""通用 REST PLM 适配器

针对 plm.agent4erp.cn（PLM系统2）：Web 登录会话 + 页面/JSON 接口。
PLM 无公开 API 文档，需用户提供登录凭据后按实际接口路径配置。

可配置（settings）：
- PLM_URL      系统地址
- PLM_API_KEY  API Key / Token（可选）
- PLM_MODEL    图纸实体路径（如 /api/documents）
"""
from __future__ import annotations

import asyncio
import logging

from app.config import Settings

logger = logging.getLogger(__name__)


class RestPlmAdapter:
    name = "rest"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.base_url = settings.PLM_URL.rstrip("/") if settings.PLM_URL else ""
        self.api_key = settings.PLM_API_KEY
        self.model_path = settings.PLM_MODEL or "/api/documents"

    async def health(self) -> dict:
        if not self.base_url:
            return {"ok": False, "configured": False, "detail": "PLM_URL 未配置"}
        try:
            # 轻量探测（跟随重定向到登录页视为可达）
            import urllib.request

            req = urllib.request.Request(self.base_url, method="GET")
            with urllib.request.urlopen(req, timeout=8) as resp:
                return {"ok": True, "configured": True, "detail": f"HTTP {resp.status}"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "configured": True, "detail": str(e)[:120]}

    async def list_documents(self, project_key: str | None = None) -> list[dict]:
        """从 PLM 拉取图纸/文档列表（需登录会话/API Key）

        占位实现：等待用户提供 PLM 接口路径与凭据后在此填充实际调用。
        """
        if not self.base_url:
            return []
        # TODO: 按 PLM 实际接口实现：
        # 1. 会话登录（用户名/密码或 API Key）
        # 2. GET {base_url}{model_path}?project={project_key}
        # 3. 转换为前端 S() 行契约
        logger.info("PLM REST 适配器待配置（URL=%s, path=%s）", self.base_url, self.model_path)
        return []

    async def get_document(self, doc_id: str) -> dict | None:
        return None


# 注册到注册表
from .registry import register  # noqa: E402

register("mock", __import__("app.services.plm.mock_adapter", fromlist=["MockPlmAdapter"]).MockPlmAdapter)
register("rest", RestPlmAdapter)
