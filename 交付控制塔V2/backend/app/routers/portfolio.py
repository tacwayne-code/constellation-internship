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

    risks, risk_source = await fetch_with_fallback(
        client, GanttAdapter(), domain=[("stage_id", "!=", False)], mock_key="risks"
    )
    risk_count = len(risks) if risk_source == "odoo" else len(await fetch_mock_rows("risks"))
    blockers = sum(p.get("blockers") or 0 for p in projects)

    summary = {
        "projects_total": len(projects),
        "projects_active": len(projects),
        "progress_avg": round(sum(p.get("progress", 0) for p in projects) / max(len(projects), 1)),
        "risks_total": risk_count,
        "blockers_total": blockers,
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
    rows, source = await fetch_with_fallback(
        client, GanttAdapter(), domain=[], mock_key="risks", limit=limit
    )
    if not rows:
        rows = await fetch_mock_rows("risks")
    if category:
        rows = [r for r in rows if r.get("category") == category]
    _mock_header(resp, source)
    return rows


@router.get("/risks/blockers")
async def list_blockers(client: ClientDep, resp: Response):
    rows, source = await fetch_with_fallback(
        client, GanttAdapter(), mock_key="risks"
    )
    if not rows:
        rows = await fetch_mock_rows("risks")
    blockers = [r for r in rows if r.get("is_blocker") or "ISS" in str(r.get("id", ""))]
    _mock_header(resp, source)
    return blockers or rows[:3]
