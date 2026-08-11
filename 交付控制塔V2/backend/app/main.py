"""交付控制塔 V2 · FastAPI 代理服务入口"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from app.config import get_settings
from app.routers import modules as modules_router
from app.routers import plm as plm_router
from app.routers import portfolio as portfolio_router
from app.routers import delivery_tower as delivery_tower_router
from app.routers import procurement as procurement_router
from app.services.cache import get_cache
from app.services.odoo.client import OdooClient

logger = logging.getLogger("delivery-control-tower")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

settings = get_settings()

app = FastAPI(
    title="交付控制塔 V2 API",
    description="Odoo 18 + PLM 数据代理服务",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Data-Source"],
)


@app.middleware("http")
async def no_cache_for_api(request: Request, call_next):
    """API 响应禁止浏览器/代理缓存（避免 React Query 看到过期数据）"""
    response: Response = await call_next(request)
    if request.url.path.startswith(f"{settings.API_PREFIX}/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Vary"] = "*"
    return response

_heartbeat_task: asyncio.Task | None = None


async def _heartbeat():
    """定期验证 Odoo 会话（TTL 25 分钟内刷新）"""
    client = OdooClient.get_instance(settings)
    while True:
        await asyncio.sleep(min(settings.SESSION_TTL - 300, 1500) if settings.SESSION_TTL > 300 else 1200)
        try:
            if client.is_configured():
                await client.authenticate()
                logger.info("Odoo 心跳：会话有效 uid=%s", client._uid)
        except Exception as e:  # noqa: BLE001
            logger.warning("Odoo 心跳失败: %s", e)


@app.on_event("startup")
async def startup():
    global _heartbeat_task
    if settings.USE_MOCK:
        logger.info("USE_MOCK=true，后端运行在离线演示模式")
        return
    if not OdooClient.get_instance(settings).is_configured():
        logger.warning("Odoo 凭据未配置（ODOO_PASSWORD 为空），health 端点将报告未就绪")
        return
    _heartbeat_task = asyncio.create_task(_heartbeat())
    logger.info("交付控制塔后端已启动（Odoo: %s/%s）", settings.ODOO_URL, settings.ODOO_DB)


@app.on_event("shutdown")
async def shutdown():
    global _heartbeat_task
    if _heartbeat_task:
        _heartbeat_task.cancel()


@app.get(f"{settings.API_PREFIX}/health", tags=["system"])
async def health():
    """健康检查：返回后端 + Odoo + 缓存状态"""
    client = OdooClient.get_instance(settings)
    odoo = {"ok": False, "configured": client.is_configured()}
    if not settings.USE_MOCK and client.is_configured():
        odoo = await client.health()

    return {
        "status": "ok",
        "use_mock": settings.USE_MOCK,
        "odoo": odoo,
        "cache": {"backend": "memory", "size": get_cache().size},
        "plm": {"adapter": settings.PLM_ADAPTER, "status": "not_configured" if settings.PLM_URL == "" else "configured"},
    }


@app.get(f"{settings.API_PREFIX}/ping", tags=["system"])
async def ping():
    return {"pong": True}


@app.get(f"{settings.API_PREFIX}/debug/status", tags=["system"])
async def debug_status():
    """诊断：确认当前后端进程身份 + Odoo 状态 + 项目数据直读"""
    client = OdooClient.get_instance(settings)
    odoo_ok = False
    projects = []
    projects_total = 0
    if not settings.USE_MOCK and client.is_configured():
        try:
            odoo_ok = True
            records = await client.search_read(
                "project.project", [], ["id", "name"], limit=50
            )
            projects = [{"id": r["id"], "name": r.get("name")} for r in records]
            projects_total = len(projects)
        except Exception as e:  # noqa: BLE001
            return {
                "pid": __import__("os").getpid(),
                "odoo": {"ok": False, "error": str(e)[:120]},
                "projects_total": 0,
                "projects": [],
                "mock_cleared": True,
            }
    return {
        "pid": __import__("os").getpid(),
        "use_mock": settings.USE_MOCK,
        "odoo": {"ok": odoo_ok, "server": settings.ODOO_URL},
        "projects_total": projects_total,
        "projects": projects,
        "mock_cleared": True,
        "timestamp": __import__("datetime").datetime.now().isoformat(),
    }


# ---- 业务路由 ----
app.include_router(portfolio_router.router)
app.include_router(modules_router.router)
app.include_router(plm_router.router)
app.include_router(delivery_tower_router.router)
app.include_router(procurement_router.router)

# ---- 前端静态资源（生产部署：后端单端口 serve dist） ----
try:
    from fastapi.staticfiles import StaticFiles

    _dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
    if _dist.is_dir() and (_dist / "index.html").exists():
        app.mount("/", StaticFiles(directory=str(_dist), html=True), name="frontend")
        logger.info("前端静态资源挂载: %s", _dist)
    else:
        logger.warning("frontend/dist 不存在，跳过静态挂载（请先执行 vite build）")
except Exception as e:  # noqa: BLE001
    logger.warning("前端静态挂载失败: %s", e)
