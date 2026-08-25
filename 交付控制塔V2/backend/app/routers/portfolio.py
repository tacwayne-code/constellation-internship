"""项目组合驾驶舱路由：/api/projects、/api/portfolio/summary、/api/risks"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from typing import Annotated

from app.config import get_settings, Settings
from app.services.adapters.base import fetch_mock_rows, fetch_with_fallback
from app.services.adapters.project import GanttAdapter, ProjectAdapter, enrich_projects, project_progress
from app.services.odoo.client import OdooClient
from app.services.odoo.models import FIELDS_TASK, MODEL_TASK

router = APIRouter(prefix="/api", tags=["portfolio"])


def get_client() -> OdooClient:
    return OdooClient.get_instance(get_settings())


ClientDep = Annotated[OdooClient, Depends(get_client)]


def _mock_header(resp: Response, source: str):
    if source == "mock":
        resp.headers["X-Data-Source"] = "mock"


@router.get("/projects")
async def list_projects(client: ClientDep, resp: Response, limit: int = Query(50, ge=1, le=500)):
    rows, source = await fetch_with_fallback(
        client, ProjectAdapter(), mock_key="projects", limit=limit
    )
    if source == "odoo" and rows:
        rows = await enrich_projects(client, rows)
    if not rows:
        rows = await fetch_mock_rows("projects")
    _mock_header(resp, source)
    return rows


@router.get("/portfolio/summary")
async def portfolio_summary(client: ClientDep, resp: Response):
    projects, source = await fetch_with_fallback(
        client, ProjectAdapter(), mock_key="projects"
    )
    if source == "odoo" and projects:
        projects = await enrich_projects(client, projects)
    if not projects:
        projects = await fetch_mock_rows("projects")

    # 任务口径：Odoo 标准模型无「风险」对象，用 project.task 的进行中 / 逾期状态作为真实指标
    tasks_active = 0
    tasks_overdue = 0
    if source == "odoo":
        from datetime import date

        tasks = await client.search_read(
            MODEL_TASK,
            [],
            ["state", "date_deadline"],
            limit=5000,
        )
        today = date.today().isoformat()
        for t in tasks:
            if t.get("state") in ("1_done", "1_canceled"):
                continue
            tasks_active += 1
            if t.get("date_deadline") and str(t["date_deadline"])[:10] < today:
                tasks_overdue += 1

    # 平均进度：仅统计有任务的项目，避免「无任务=0%」拉低均值
    scored = [p for p in projects if p.get("_has_tasks")]
    progress_avg = round(sum(p.get("progress", 0) for p in scored) / max(len(scored), 1))
    for p in projects:
        p.pop("_has_tasks", None)

    summary = {
        "projects_total": len(projects),
        "projects_active": len(projects),
        "progress_avg": progress_avg,
        "tasks_active": tasks_active,
        "tasks_overdue": tasks_overdue,
        "by_tone": {
            "green": sum(1 for p in projects if p.get("tone") == "success"),
            "amber": sum(1 for p in projects if p.get("tone") == "warning"),
            "red": sum(1 for p in projects if p.get("tone") == "danger"),
        },
        "projects": projects,
    }
    _mock_header(resp, source)
    return summary


@router.get("/projects/{project_id}")
async def get_project(project_id: str, client: ClientDep, resp: Response):
    rows, source = await fetch_with_fallback(
        client, ProjectAdapter(), mock_key="projects"
    )
    if source == "odoo" and rows:
        rows = await enrich_projects(client, rows)
    if not rows:
        rows = await fetch_mock_rows("projects")
    match = next((p for p in rows if p["id"] == project_id), None)
    _mock_header(resp, source)
    if match is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=f"项目 {project_id} 不存在")
    return match


@router.get("/projects/{project_id}/gantt")
async def project_gantt(project_id: str, client: ClientDep, resp: Response, limit: int = Query(100, ge=1, le=500)):
    odoo_pid = int(project_id[1:]) if project_id.startswith("p") and project_id[1:].isdigit() else None
    domain = [("project_id", "=", odoo_pid)] if odoo_pid else []
    rows, source = await fetch_with_fallback(
        client, GanttAdapter(), domain=domain, mock_key="risks",
        limit=limit, project_id=str(odoo_pid or project_id),
    )
    if not rows:
        # Mock 甘特：从 risks mock 生成（演示态）
        rows = await fetch_mock_rows("risks")
    _mock_header(resp, source)
    return rows


@router.get("/risks")
async def list_risks(client: ClientDep, resp: Response, category: str | None = None, limit: int = Query(500, ge=1, le=500)):
    # 进行中任务（未完成且未取消），作为「进行中任务」列表
    domain = [("state", "not in", ["1_done", "1_canceled"])]
    rows, source = await fetch_with_fallback(
        client, GanttAdapter(), domain=domain, mock_key="risks", limit=limit
    )
    if not rows:
        rows = await fetch_mock_rows("risks")
    if category:
        rows = [r for r in rows if r.get("category") == category]
    _mock_header(resp, source)
    return rows


@router.get("/risks/blockers")
async def list_blockers(client: ClientDep, resp: Response):
    # 逾期任务（未完成且已过截止日）
    from datetime import date

    today = date.today().isoformat()
    domain = [
        ("state", "not in", ["1_done", "1_canceled"]),
        ("date_deadline", "<", today),
    ]
    rows, source = await fetch_with_fallback(
        client, GanttAdapter(), domain=domain, mock_key="risks"
    )
    if not rows:
        rows = await fetch_mock_rows("risks")
    _mock_header(resp, source)
    return rows
