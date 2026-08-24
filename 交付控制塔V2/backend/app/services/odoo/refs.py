"""Odoo XML-RPC m2o/m2m 引用解析工具（_ref_id / _ref_name 公共化）

统一处理 Odoo 18 XML-RPC 返回的 m2o/m2m 引用：
- m2o 通常返回 [id, name] 元组（或纯 id）
- m2m 返回纯 id 列表 [id1, id2, ...] 或 [(id, name), ...]
"""
from __future__ import annotations

from typing import Any


def _ref_id(ref: Any) -> int | None:
    """兼容 Odoo 18 m2o/m2m：返回 id 或 None"""
    if isinstance(ref, (list, tuple)):
        return ref[0] if ref else None
    if isinstance(ref, int):
        return ref
    return None


def _ref_name(ref: Any) -> str:
    """兼容 Odoo 18 m2o：返回显示名；无则返回 "—" """
    if isinstance(ref, (list, tuple)) and len(ref) > 1:
        return str(ref[1])
    return str(ref) if ref else "—"
