"""Odoo 状态 → 前端 tone 映射中心

tone 值域（前端契约）：success / warning / danger / neutral
辅助色：blue / purple / orange / green / red（用于图表与模块卡）
"""
from __future__ import annotations

# (model, state) → tone
DOMAIN_STATE_TO_TONE: dict[tuple[str, str], str] = {
    # ---- project.task ----
    ("project.task", "01_in_progress"): "warning",
    ("project.task", "1_done"): "success",
    ("project.task", "03_approved"): "success",
    ("project.task", "02_changes_requested"): "danger",
    ("project.task", "04_waiting_normal"): "warning",
    ("project.task", "1_open"): "neutral",
    ("project.task", "2_progress"): "warning",
    ("project.task", "3_done"): "success",
    ("project.task", "3_cancel"): "neutral",
    # kanban_state
    ("project.task", "normal"): "warning",
    ("project.task", "done"): "success",
    ("project.task", "blocked"): "danger",
    # ---- project.project（自定义阶段名兜底）----
    ("project.project", "green"): "success",
    ("project.project", "amber"): "warning",
    ("project.project", "red"): "danger",
    # ---- purchase.order ----
    ("purchase.order", "draft"): "neutral",
    ("purchase.order", "sent"): "warning",
    ("purchase.order", "to approve"): "warning",
    ("purchase.order", "purchase"): "warning",
    ("purchase.order", "locked"): "orange",
    ("purchase.order", "received"): "success",
    ("purchase.order", "done"): "success",
    ("purchase.order", "cancel"): "neutral",
    # ---- stock.picking ----
    ("stock.picking", "draft"): "neutral",
    ("stock.picking", "waiting"): "neutral",
    ("stock.picking", "confirmed"): "warning",
    ("stock.picking", "assigned"): "warning",
    ("stock.picking", "done"): "success",
    ("stock.picking", "cancel"): "neutral",
    # ---- hr.employee / 班组 ----
    ("hr.employee", "on_site"): "success",
    ("hr.employee", "off_site"): "neutral",
    ("hr.employee", "pending"): "warning",
    ("hr.employee", "remote"): "blue",
    # ---- res.partner 供应商 ----
    ("res.partner", "ok"): "success",
    ("res.partner", "watch"): "warning",
    ("res.partner", "risk"): "danger",
    ("res.partner", "pending_docs"): "warning",
}

# 状态 → 中文标签
STATE_LABELS: dict[tuple[str, str], str] = {
    ("project.task", "01_in_progress"): "进行中",
    ("project.task", "1_done"): "已完成",
    ("project.task", "03_approved"): "已批准",
    ("project.task", "02_changes_requested"): "需修改",
    ("project.task", "1_open"): "待开始",
    ("project.task", "2_progress"): "进行中",
    ("project.task", "3_done"): "已完成",
    ("project.task", "3_cancel"): "已取消",
    ("purchase.order", "draft"): "草稿",
    ("purchase.order", "sent"): "询价中",
    ("purchase.order", "to approve"): "待批准",
    ("purchase.order", "purchase"): "采购中",
    ("purchase.order", "locked"): "已锁单",
    ("purchase.order", "received"): "收货中",
    ("purchase.order", "done"): "已完成",
    ("purchase.order", "cancel"): "已取消",
    ("stock.picking", "draft"): "草稿",
    ("stock.picking", "waiting"): "等待",
    ("stock.picking", "confirmed"): "已确认",
    ("stock.picking", "assigned"): "准备中",
    ("stock.picking", "done"): "已签收",
    ("stock.picking", "cancel"): "已取消",
}

# 采购/库存/项目的 stage 名 → tone 启发式（当 state 不在表内时按关键字匹配）
KEYWORD_TONE: list[tuple[str, str]] = [
    ("完成", "success"), ("done", "success"), ("closed", "success"),
    ("通过", "success"), ("approved", "success"),
    ("进行", "warning"), ("in_progress", "warning"), ("progress", "warning"),
    ("测试", "warning"), ("联调", "warning"),
    ("风险", "danger"), ("阻塞", "danger"), ("blocked", "danger"),
    ("逾期", "danger"), ("超期", "danger"),
    ("待", "neutral"), ("pending", "neutral"), ("草稿", "neutral"), ("draft", "neutral"),
    ("取消", "neutral"), ("cancel", "neutral"),
]


def map_tone(model: str, state: str) -> str:
    """精确映射 + 关键字兜底"""
    if not state:
        return "neutral"
    tone = DOMAIN_STATE_TO_TONE.get((model, state))
    if tone:
        return tone
    for kw, t in KEYWORD_TONE:
        if kw in state:
            return t
    return "neutral"


def state_label(model: str, state: str) -> str:
    """状态 → 中文标签，找不到时原样返回"""
    return STATE_LABELS.get((model, state), state or "—")


def tone_for_selection(value: str) -> str:
    """通用 selection → tone（用于自定义字段）"""
    mapping = {
        "good": "success", "ok": "success", "green": "success", "passed": "success",
        "watch": "warning", "amber": "warning", "pending": "warning", "testing": "warning",
        "risk": "danger", "red": "danger", "blocked": "danger", "failed": "danger",
    }
    return mapping.get(value, "neutral")
