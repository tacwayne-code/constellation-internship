"""PLM 适配器注册表 + 获取当前适配器"""
from __future__ import annotations

from typing import Any

from app.config import Settings

_registry: dict[str, type] = {}


def register(name: str, adapter_cls: type):
    _registry[name] = adapter_cls


def _ensure_registry():
    """lazy 加载所有适配器（触发其 register 调用）"""
    if _registry:
        return
    from . import mock_adapter  # noqa: F401
    from . import rest_adapter  # noqa: F401


def get_plm_adapter(settings: Settings) -> Any:
    """按 settings.PLM_ADAPTER 获取适配器实例；未配置返回 mock"""
    _ensure_registry()
    name = (settings.PLM_ADAPTER or "mock").lower()
    cls = _registry.get(name)
    if cls is None:
        from .mock_adapter import MockPlmAdapter

        cls = MockPlmAdapter
    return cls(settings)
