"""日期格式转换工具（Odoo → 前端 MM/DD 契约）"""
from __future__ import annotations

from datetime import datetime

DATE_ONLY = "%Y-%m-%d"


def parse_odoo_date(value: str | None) -> datetime | None:
    """解析 Odoo 日期字符串（含 datetime 或纯日期）"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        try:
            return datetime.strptime(str(value)[:10], DATE_ONLY)
        except ValueError:
            return None


def to_mmdd(value: str | None) -> str:
    """2026-08-07 → '08/07'；无效值返回 '—'"""
    dt = parse_odoo_date(value)
    if dt is None:
        return "—"
    return dt.strftime("%m/%d")


def to_range(start: str | None, end: str | None) -> str:
    """'07/15 - 07/29'"""
    s, e = to_mmdd(start), to_mmdd(end)
    if s == "—" and e == "—":
        return "—"
    return f"{s} - {e}"


def to_mmdd_hhmm(value: str | None) -> str:
    """datetime → '08/09 10:00'"""
    dt = parse_odoo_date(value)
    if dt is None:
        return "—"
    return dt.strftime("%m/%d %H:%M")


def to_hhmm(value: str | None) -> str:
    """datetime → '10:42'"""
    dt = parse_odoo_date(value)
    if dt is None:
        return "—"
    return dt.strftime("%H:%M")


def gantt_position(start: str | None, end: str | None, project_start: str | None = None,
                   project_end: str | None = None) -> tuple[float, float]:
    """甘特图 start/width 百分比映射

    以项目计划区间为时间轴（缺省用任务自身区间），将任务线性映射为 [start%, width%]
    """
    s, e = parse_odoo_date(start), parse_odoo_date(end)
    ps = parse_odoo_date(project_start)
    pe = parse_odoo_date(project_end)

    if s is None or e is None:
        return (0.0, 0.0)
    # 反向（begin > end）自动交换（已完成后阶段更新晚于截止）
    if e < s:
        s, e = e, s
    if ps is None:
        ps = s
    if pe is None or pe <= ps:
        pe = e
    span = (pe - ps).total_seconds() or 1.0
    start_pct = max((s - ps).total_seconds() / span * 100, 0.0)
    width_pct = min((e - s).total_seconds() / span * 100, 100.0 - start_pct)
    return round(start_pct, 1), round(max(width_pct, 3.0), 1)
