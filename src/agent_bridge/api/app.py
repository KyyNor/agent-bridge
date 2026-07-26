from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from agent_bridge.api.dashboard_proxy import DashboardProxyMiddleware, MemoryDashboardProxyMiddleware
from agent_bridge.core.config import AgentBridgePaths, default_user, load_logging_config, load_server_config
from agent_bridge.core.logging import setup_logging
from agent_bridge.core.domain import AgentBridgeError
from agent_bridge.automation.workflows.validation import WorkflowDefinitionValidationError
from agent_bridge.app.service import AgentBridgeService
from agent_bridge.web.pages import capability_admin_page

logger = logging.getLogger(__name__)


def create_app(paths: AgentBridgePaths | None = None, admins: set[str] | None = None) -> FastAPI:
    resolved_paths = paths or AgentBridgePaths.from_root()
    # 尽早初始化日志（早于 Service 装配，使构造期日志也被捕获）；
    # 参数来自 server.toml 的 [logging] 段（缺失则用默认值）
    setup_logging(resolved_paths, config=load_logging_config(resolved_paths))
    resolved_admins = admins if admins is not None else load_server_config(resolved_paths).admins
    service = AgentBridgeService.create(resolved_paths, resolved_admins)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """应用生命周期：初始化存储、对齐后端、后台刷新托管插件、启动调度器，停止时逆序收尾。"""
        logger.info("Agent Bridge 服务启动 root=%s", resolved_paths.root)
        service.store.init_schema()
        service.store.set_runtime_log_retention_days(int(service.store.get_sync_config().get("log_retention_days") or 180))
        deleted_logs = service.store.prune_runtime_logs(force=True)
        if any(deleted_logs.values()):
            logger.info("运行日志清理完成 tool_call_logs=%d agent_runs=%d", deleted_logs["tool_call_logs"], deleted_logs["agent_runs"])
        try:
            service.align_backends()
        except Exception:
            # 后端对齐失败不阻断启动：个别检索后端不可用时仍允许 /health 就绪
            logger.warning("align_backends 后端对齐失败，跳过", exc_info=True)

        # Managed plugin refresh (git pull on understand-anything / claude-mem)
        # does blocking network I/O, so run it in the background instead of
        # gating /health readiness — otherwise startup blocks ~3-4s (or up to
        # ~60s per repo on a slow/unreachable git host).
        async def _refresh_managed_plugins() -> None:
            try:
                results = await asyncio.to_thread(service.ensure_managed_plugins)
                logger.info("托管插件后台刷新完成 result=%s", results)
            except Exception:
                logger.warning("启动后台刷新托管插件失败", exc_info=True)

        plugin_task = asyncio.create_task(_refresh_managed_plugins())

        # 后台检查各文档知识 adapter 声明的托管资源，不阻塞就绪。
        async def _ensure_backend_resources() -> None:
            try:
                await asyncio.to_thread(service.ensure_backend_resources)
            except Exception:
                logger.warning("启动时文档知识后端托管资源自愈失败", exc_info=True)

        asyncio.create_task(_ensure_backend_resources())

        service.codegraph_scheduler.start()
        service.understand_scheduler.start()
        service.plugin_update_scheduler.start()
        service.doc_sync_scheduler.start()
        service.workflow_scheduler.start()
        logger.info(
            "调度器已启动 codegraph/understand/plugin_update/doc_sync/workflow"
        )
        yield
        logger.info("Agent Bridge 服务停止 root=%s", resolved_paths.root)
        if not plugin_task.done():
            plugin_task.cancel()
        service.codegraph.ua_client.stop_all_dashboards()
        try:
            service.memory.worker_service.stop_all_workers()
        except Exception:
            logger.warning("停止 claude-mem worker 失败", exc_info=True)
        service.codegraph_scheduler.stop()
        service.understand_scheduler.stop()
        service.plugin_update_scheduler.stop()
        service.doc_sync_scheduler.stop()
        service.workflow_scheduler.stop()
        logger.info("Agent Bridge 服务已停止")

    app = FastAPI(title="Agent Bridge", docs_url=None, openapi_url=None, redoc_url=None, lifespan=lifespan)
    app.state.agent_bridge_service = service
    app.add_middleware(
        DashboardProxyMiddleware,
        target_resolver=service.codegraph.dashboard_proxy_target,
        token_resolver=service.codegraph.dashboard_repo_by_token,
    )
    app.add_middleware(
        MemoryDashboardProxyMiddleware,
        target_resolver=service.memory.dashboard_proxy_target,
    )
    static_dir = Path(__file__).parent.parent / "static" / "capabilities"
    app.mount("/static/capabilities", StaticFiles(directory=static_dir, check_dir=False), name="capabilities-static")

    @app.get("/")
    def root() -> RedirectResponse:
        return RedirectResponse(url="/admin/capabilities", status_code=307)

    def actor(x_agent_bridge_user: str = Header(alias="X-Agent-Bridge-User")) -> str:
        return x_agent_bridge_user

    @app.exception_handler(AgentBridgeError)
    async def _handle_agent_bridge_error(request: Request, exc: AgentBridgeError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

    @app.exception_handler(WorkflowDefinitionValidationError)
    async def _handle_workflow_validation_error(request: Request, exc: WorkflowDefinitionValidationError) -> JSONResponse:
        from dataclasses import asdict

        return JSONResponse(
            status_code=400,
            content={"detail": exc.message, "errors": [asdict(issue) for issue in exc.issues]},
        )

    if admins is None:
        @app.middleware("http")
        async def _reload_admins_if_dynamic(request: Request, call_next):
            reloaded = load_server_config(resolved_paths).admins
            service.admins = reloaded
            service.capabilities.admins = reloaded
            service.governance.admins = reloaded
            service.codegraph.admins = reloaded
            service.workflows.admins = reloaded
            service.skills.admins = reloaded
            service.scripts.admins = reloaded
            service.memory.admins = reloaded
            service.plugin_update_scheduler._admins = reloaded
            return await call_next(request)

    def save_upload(file: UploadFile) -> Path:
        resolved_paths.run_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(upload_filename(file)).suffix
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, dir=resolved_paths.run_dir, suffix=suffix) as output:
                tmp_path = Path(output.name)
                shutil.copyfileobj(file.file, output)
            return tmp_path
        except Exception:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
            raise

    def upload_filename(file: UploadFile) -> str:
        return Path((file.filename or "upload").replace("\\", "/")).name or "upload"

    def catalog_sources(current_actor: str, profile_key: str | None, query: str | None) -> list[dict[str, Any]]:
        mcp_items = service.capabilities.list_service_summaries(current_actor)
        source_keys = [item["service_key"] for item in mcp_items]
        allowed_keys = set(
            service.governance.filter_source_keys(
                actor=current_actor, profile_key=profile_key, source_type="mcp_service", source_keys=source_keys,
            )
        )
        sources = []
        for item in mcp_items:
            if item["service_key"] not in allowed_keys:
                continue
            tags = item.get("tags", [])
            text = f"{item['service_key']} {item['name']} {item.get('description', '')} {' '.join(tags)}".lower()
            if query and query.lower() not in text:
                continue
            sources.append({"source_type": "mcp_service", "source_key": item["service_key"], "name": item["name"], "description": item["description"], "status": item["status"], "tags": tags})
        openapi_items = service.capabilities.list_openapi_service_summaries(current_actor)
        openapi_source_keys = [item["service_key"] for item in openapi_items]
        allowed_openapi_keys = set(
            service.governance.filter_source_keys(
                actor=current_actor,
                profile_key=profile_key,
                source_type="openapi_service",
                source_keys=openapi_source_keys,
            )
        )
        for item in openapi_items:
            if item["service_key"] not in allowed_openapi_keys:
                continue
            tags = item.get("tags", [])
            text = f"{item['service_key']} {item['name']} {item.get('description', '')} {' '.join(tags)}".lower()
            if query and query.lower() not in text:
                continue
            sources.append({"source_type": "openapi_service", "source_key": item["service_key"], "name": item["name"], "description": item["description"], "status": item["status"], "tags": tags})
        return sources

    # Register route modules
    from agent_bridge.api.routes.health import router as health_router
    app.include_router(health_router)

    from agent_bridge.api.routes.knowledge import create_knowledge_routes
    app.include_router(create_knowledge_routes(service, actor, save_upload, upload_filename))

    from agent_bridge.api.routes.capabilities import create_capability_routes
    app.include_router(create_capability_routes(service, actor, catalog_sources))

    from agent_bridge.api.routes.governance import create_governance_routes
    app.include_router(create_governance_routes(service, actor))

    from agent_bridge.api.routes.agent_runs import create_agent_runs_routes
    app.include_router(create_agent_runs_routes(service, actor))

    from agent_bridge.api.routes.builtins import create_builtin_routes
    app.include_router(create_builtin_routes(service, actor))

    from agent_bridge.api.routes.workflows import create_workflow_routes
    app.include_router(create_workflow_routes(service, actor))

    from agent_bridge.api.routes.script_runtime import create_script_runtime_routes
    app.include_router(create_script_runtime_routes(service, actor))

    from agent_bridge.api.routes.memory import create_memory_routes
    app.include_router(create_memory_routes(service, actor))

    from agent_bridge.api.routes.retrieval_probe import create_retrieval_probe_routes
    app.include_router(create_retrieval_probe_routes(service, actor))

    # MCP streamable HTTP endpoint
    from agent_bridge.capability_hub.gateway.metamcp import setup_mcp_route
    setup_mcp_route(app, service)

    @app.get("/admin/capabilities", response_class=HTMLResponse)
    def capability_admin() -> HTMLResponse:
        return HTMLResponse(content=capability_admin_page(default_user()), media_type="text/html")

    return app
