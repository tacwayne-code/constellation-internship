"""采购看板 API：所有 PO + priority 分组 + 状态统计"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response

from app.config import get_settings
from app.services.odoo.client import OdooClient
from app.services.odoo.models import (
    FIELDS_PURCHASE,
    MODEL_PURCHASE,
    PRIORITY_URGENT,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/delivery-tower/procurement", tags=["procurement-board"])


def get_client() -> OdooClient:
    return OdooClient.get_instance(get_settings())


ClientDep = Annotated[OdooClient, Depends(get_client)]


def _ref_name(ref: Any) -> str:
    if isinstance(ref, (list, tuple)) and len(ref) > 1:
        return str(ref[1])
    return str(ref) if ref else "—"


@router.get("/overview")
async def procurement_overview(
    client: ClientDep,
    resp: Response,
    limit: int = Query(500, ge=1, le=1000),
    urgent_only: bool = Query(False, description="只看 priority=3 紧急"),
    state: str | None = Query(None, description="按状态过滤: draft/sent/purchase/done/cancel"),
):
    """采购看板：所有采购需求 + priority 分组 + 状态统计

    返回：
      {
        "stats": {"by_priority": {3:N, 2:N, 1:N, 0:N}, "by_state": {...}, "total": N, "urgent": N},
        "by_priority": {"3": [...POs], "2": [...], "0": [...]},
        "urgent_kanban": [红组专用列表],
        "items": [全部 PO 平面]
      }
    """
    domain: list = [["state", "not in", ["cancel"]]]
    if urgent_only:
        domain.append(["priority", "=", PRIORITY_URGENT])
    if state:
        domain.append(["state", "=", state])

    pos = await client.search_read(
        MODEL_PURCHASE, domain,
        FIELDS_PURCHASE + ["order_line"],
        limit=limit, order="priority desc, date_planned asc, id desc",
    )

    items = []
    by_priority: dict[str, list] = defaultdict(list)
    state_counter: Counter[str] = Counter()
    priority_counter: Counter[int] = Counter()
    urgent_list = []

    for p in pos:
        priority = p.get("priority") or 0
        state_val = p.get("state") or "—"
        is_urgent = priority == PRIORITY_URGENT
        item = {
            "id": p["id"],
            "name": p.get("name"),
            "partner": _ref_name(p.get("partner_id")),
            "date_order": p.get("date_order"),
            "date_planned": p.get("date_planned"),
            "state": state_val,
            "priority": priority,
            "is_urgent": is_urgent,
            "amount_total": p.get("amount_total"),
            "currency": _ref_name(p.get("currency_id")),
            "user": _ref_name(p.get("user_id")),
            "project": _ref_name(p.get("project_id")),
            "line_count": len(p.get("order_line") or []),
        }
        items.append(item)
        by_priority[str(priority)].append(item)
        state_counter[state_val] += 1
        priority_counter[priority] += 1
        if is_urgent:
            urgent_list.append(item)

    urgent_list.sort(key=lambda x: x.get("date_planned") or "")
    for k in by_priority:
        by_priority[k].sort(key=lambda x: x.get("date_planned") or "")

    resp.headers["X-Data-Source"] = "odoo" if items else "empty"
    return {
        "stats": {
            "total": len(items),
            "urgent": len(urgent_list),
            "by_priority": {"1": priority_counter.get("1", 0), "0": priority_counter.get("0", 0)},
            "by_state": dict(state_counter),
        },
        "by_priority": dict(by_priority),
        "urgent_kanban": urgent_list,
        "items": items,
    }