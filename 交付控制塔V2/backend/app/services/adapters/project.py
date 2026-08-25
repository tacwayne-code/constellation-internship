"""项目组合与甘特图适配器（project.project / project.task）"""
from __future__ import annotations

import math

from app.core.date_util import gantt_position, to_mmdd, to_range
from app.core.tone import map_tone, state_label
from app.services.adapters.base import BaseRowAdapter, _fmt_owner
from app.services.odoo.models import (
    FIELDS_PROJECT,
    FIELDS_TASK,
    MODEL_PROJECT,
    MODEL_TASK,
)

_CN_WEEK = ["", "一", "二", "三", "四", "五", "六", "日"]


def _week_label(date_str: str) -> str:
    import datetime

    try:
        dt = datetime.date.fromisoformat(str(date_str)[:10])
        return f"第 {dt.isocalendar()[1]} 周"
    except (ValueError, TypeError):
        return ""


class ProjectAdapter(BaseRowAdapter):
    model = MODEL_PROJECT
    fields = FIELDS_PROJECT

    def to_row(self, record: dict, project_id: str | None = None) -> dict:
        stage = record.get("stage_id")
        stage_name = stage[1] if isinstance(stage, (list, tuple)) and len(stage) > 1 else ""

        # 状态推断：优先 stage 名关键字，默认绿灯
        tone = map_tone(MODEL_PROJECT, stage_name or "")
        if tone == "neutral":
            tone = "success"

        # 进度：task_count 无法直接算完成率，用 stage 名数字启发式（xx% 或数字）
        progress = 0
        phase = stage_name or "规划中"
        due = to_mmdd(record.get("date"))
        date_start = to_mmdd(record.get("date_start"))

        return {
            "id": f"p{record['id']}",
            "name": record.get("name", "未命名项目"),
            "short": record.get("name", "")[:6],
            "type": "工程项目",
            "owner": _fmt_owner(record.get("user_id")),
            "status": {"success": "绿灯", "warning": "黄灯", "danger": "红灯"}.get(tone, "绿灯"),
            "tone": tone,
            "progress": progress,
            "active": 0,
            "overdue": 0,
            "due": due,
            "phase": phase,
            "start": date_start,
            "stage": stage_name,
            "fields": [
                ["项目负责人", _fmt_owner(record.get("user_id"))],
                ["开始日期", date_start],
                ["截止日期", due],
                ["当前阶段", phase],
            ],
        }


class GanttAdapter(BaseRowAdapter):
    """甘特任务：Odoo 18 project.task（user_ids m2m / state / planned_date_begin）"""

    model = MODEL_TASK
    fields = FIELDS_TASK

    def to_row(self, record: dict, project_id: str | None = None) -> dict:
        state = record.get("state") or ""
        tone = map_tone(MODEL_TASK, state)
        # 日期优先级：planned_date_begin → date_last_stage_update → create_date
        # 结束：date_end → date_deadline
        date_begin = (
            record.get("planned_date_begin")
            or record.get("date_last_stage_update")
            or record.get("create_date")
        )
        date_end = record.get("date_end") or record.get("date_deadline")
        # 如果只有 begin 没有 end，给 14 天默认跨度
        if date_begin and not date_end:
            try:
                from datetime import datetime, timedelta
                dt = datetime.fromisoformat(str(date_begin).replace("Z", "+00:00").replace(" ", "T"))
                date_end = (dt + timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                date_end = date_begin
        # 如果只有 end 没有 begin，回退 14 天作为开始
        elif date_end and not date_begin:
            try:
                from datetime import datetime, timedelta
                dt = datetime.fromisoformat(str(date_end).replace("Z", "+00:00").replace(" ", "T"))
                date_begin = (dt - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
        # 同日期：向前推 7 天，使条有一定宽度
        elif date_begin and date_end:
            try:
                from datetime import datetime, timedelta
                bs = datetime.fromisoformat(str(date_begin).replace("Z", "+00:00").replace(" ", "T"))
                be = datetime.fromisoformat(str(date_end).replace("Z", "+00:00").replace(" ", "T"))
                if be == bs:
                    date_begin = (bs - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
                    date_end = be.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
        start, width = gantt_position(date_begin, date_end)
        # Odoo 18 默认 progress 全 0，用 state 推断完成度
        progress = record.get("progress") or 0
        if state == "1_done":
            progress = 100
        elif progress == 0 and state == "01_in_progress":
            progress = 50

        # user_ids：Odoo 18 返回 flat id list [163]；fetch_with_fallback 已注入 _user_names = [{id, name}, ...]
        owner = "—"
        user_ids = record.get("user_ids") or []
        if user_ids:
            names_list = record.get("_user_names") or []
            if names_list and isinstance(names_list[0], dict):
                owner = str(names_list[0].get("name") or "—")
            else:
                # 兼容 fallback：dict 解析失败
                first = user_ids[0]
                if isinstance(first, dict):
                    owner = str(first.get("name") or first.get("id") or "—")
                elif isinstance(first, (list, tuple)) and len(first) > 1:
                    owner = str(first[1])
                else:
                    owner = f"用户#{first}"

        # 阶段：stage_id 可能是 [id, name]
        stage = record.get("stage_id") or record.get("personal_stage_id")
        stage_name = stage[1] if isinstance(stage, (list, tuple)) and len(stage) > 1 else "—"

        # 父任务
        parent = record.get("parent_id")
        parent_name = parent[1] if isinstance(parent, (list, tuple)) and len(parent) > 1 else ""

        # 所属项目
        proj = record.get("project_id")
        proj_name = proj[1] if isinstance(proj, (list, tuple)) and len(proj) > 1 else ""

        # 创建时间
        created = record.get("create_date") or ""
        created_date = (str(created)[:10]) if created else "—"

        # 优先级
        priority = record.get("priority", "1")
        priority_label = {0: "低", "1": "正常", "2": "高"}.get(priority, str(priority))

        # 工时（Odoo Timesheet 字段）
        effective = record.get("effective_hours") or 0
        remaining = record.get("remaining_hours") or 0
        total = record.get("total_hours_spent") or 0

        # 标签：tag_ids 是 m2m → [[id, name], ...]
        tags = record.get("tag_ids") or []
        tag_names = []
        for t in tags:
            if isinstance(t, (list, tuple)) and len(t) > 1:
                tag_names.append(str(t[1]))
        tag_str = ", ".join(tag_names) if tag_names else "—"

        # 最近阶段更新时间
        last_stage = record.get("date_last_stage_update") or ""
        last_stage_date = (str(last_stage)[:16]) if last_stage else "-"

        return {
            "id": f"G-P{project_id}-{record['id']}" if project_id else f"T-{record['id']}",
            "name": record.get("name", "未命名任务"),
            "owner": owner,
            "tags": tag_names,
            "start": start,
            "width": width,
            "progress": progress,
            "status": state_label(MODEL_TASK, state or "01_in_progress"),
            "stage": stage_name,
            "tone": tone,
            "_date_begin": date_begin,
            "_date_end": date_end,
            "hours": {
                "effective": effective,
                "remaining": remaining,
                "total": total,
            },
            "fields": [
                ["负责人", owner],
                ["任务阶段", stage_name],
                ["所属项目", proj_name],
                ["标签", tag_str],
                ["计划区间", to_range(record.get("planned_date_begin"), record.get("date_end"))],
                ["截止日期", to_mmdd(record.get("date_deadline"))],
                ["优先级", priority_label],
                ["父任务", parent_name or "—"],
                ["创建时间", created_date],
                ["最近阶段更新", last_stage_date],
                ["已花费工时", f"{effective:.1f} h" if effective else "—"],
                ["剩余工时", f"{remaining:.1f} h" if remaining else "—"],
                ["累计工时", f"{total:.1f} h" if total else "—"],
            ],
        }


def project_progress(records: list[dict]) -> int:
    """由任务记录估算项目进度（已完成任务占比）"""
    if not records:
        return 0
    done = sum(
        1 for r in records
        if (r.get("kanban_state") == "done")
        or (isinstance(r.get("stage_id"), (list, tuple)) and len(r["stage_id"]) > 1
            and "done" in str(r["stage_id"][1]).lower())
        or (r.get("progress") or 0) >= 100
    )
    return round(done / len(records) * 100)


async def enrich_projects(client, projects: list[dict], today: str | None = None) -> list[dict]:
    """用 project.task 统计增强项目：真实进度 / 活跃任务 / 逾期任务 / 最晚截止日 / tone

    Odoo 18 标准模型下 project.project 无进度/风险字段，从任务聚合计算。
    """
    from datetime import date

    from collections import defaultdict

    tasks = await client.search_read(
        MODEL_TASK,
        [("project_id", "!=", False)],
        ["project_id", "state", "date_deadline", "progress"],
        limit=2000,
    )
    by_project: dict[int, list] = defaultdict(list)
    for t in tasks:
        pid = t.get("project_id")
        if isinstance(pid, (list, tuple)) and pid:
            by_project[int(pid[0])].append(t)
        elif isinstance(pid, int):
            by_project[pid].append(t)

    today_iso = today or date.today().isoformat()

    for p in projects:
        odoo_id = int(p["id"][1:]) if p.get("id", "").startswith("p") else None
        ts = by_project.get(odoo_id, [])
        if not ts:
            continue
        done = sum(1 for t in ts if t.get("state") == "1_done" or (t.get("progress") or 0) >= 100)
        active = sum(1 for t in ts if t.get("state") not in ("1_done", "1_canceled"))
        overdue = sum(
            1 for t in ts
            if t.get("state") not in ("1_done", "1_canceled")
            and t.get("date_deadline")
            and str(t["date_deadline"])[:10] < today_iso
        )

        p["progress"] = round(done / len(ts) * 100)
        p["active"] = active
        p["overdue"] = overdue
        p["_has_tasks"] = True

        deadlines = [str(t["date_deadline"])[:10] for t in ts if t.get("date_deadline")]
        if deadlines:
            p["due"] = to_mmdd(max(deadlines))

        # tone 推断：有逾期 → 红灯；有进行中 → 黄灯；全完成 → 绿灯
        if overdue > 0:
            p["tone"], p["status"] = "danger", "红灯"
        elif active > 0:
            p["tone"], p["status"] = "warning", "黄灯"
        else:
            p["tone"], p["status"] = "success", "绿灯"
    return projects
