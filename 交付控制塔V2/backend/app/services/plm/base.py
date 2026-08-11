"""PLM 适配器协议"""
from __future__ import annotations

from typing import Protocol


class PlmAdapter(Protocol):
    """所有 PLM 适配器必须实现此协议"""

    name: str

    async def health(self) -> dict:
        """返回连接状态：{"ok": bool, "detail": str}"""

    async def list_documents(self, project_key: str | None = None) -> list[dict]:
        """列出设计文档/图纸（前端 design 板块行数据，符合 S() 契约）"""

    async def get_document(self, doc_id: str) -> dict | None:
        """获取文档详情"""
