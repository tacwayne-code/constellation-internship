"""Mock 数据服务：从 mock_data/*.json 加载离线降级数据"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DATA_DIR: Path | None = None
_loaded: dict[str, Any] = {}


def _get_data_dir() -> Path:
    global _DATA_DIR
    if _DATA_DIR is None:
        from app.config import get_settings

        settings = get_settings()
        _DATA_DIR = Path(settings.MOCK_DATA_DIR)
    return _DATA_DIR


def load_mock(key: str) -> list[dict]:
    """按 key 加载 Mock 数据（projects/delivery_packages/risks/procurement/logistics/inventory/people/vendors）"""
    global _loaded
    if key in _loaded:
        return _loaded[key]

    filename = {
        "projects": "projects.json",
        "delivery_packages": "delivery_packages.json",
        "risks": "risks.json",
        "procurement": "procurement.json",
        "logistics": "logistics.json",
        "inventory": "inventory.json",
        "people": "people.json",
        "vendors": "vendors.json",
        "bom": "bom.json",
        "sales": "sales.json",
        "products": "products.json",
        "manufacturing": "manufacturing.json",
        "workshop": "workshop.json",
    }.get(key)

    if not filename:
        return []

    path = _get_data_dir() / filename
    if not path.exists():
        logger.warning("Mock 数据文件不存在: %s", path)
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _loaded[key] = data
        return data
    except Exception as e:  # noqa: BLE001
        logger.error("Mock 数据加载失败 %s: %s", path, e)
        return []


def mock_rows(module: str) -> list[dict]:
    """模块行数据（与后端 S() 契约一致，直接透传）"""
    key_map = {
        "delivery": "delivery_packages",
        "design": "bom",
        "procurement": "procurement",
        "logistics": "logistics",
        "inventory": "inventory",
        "people": "people",
        "vendors": "vendors",
        "sales": "sales",
        "products": "products",
        "manufacturing": "manufacturing",
        "workshop": "workshop",
    }
    key = key_map.get(module)
    return load_mock(key) if key else []


def mock_projects() -> list[dict]:
    return load_mock("projects")


def mock_risks() -> list[dict]:
    return load_mock("risks")
