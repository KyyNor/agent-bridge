from __future__ import annotations

import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import FastAPI, Header, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from agent_bridge.api.dashboard_proxy import DashboardProxyMiddleware
from agent_bridge.core.config import AgentBridgePaths, default_user, load_server_config
from agent_bridge.core.domain import AgentBridgeError
from agent_bridge.knowledge.service import AgentBridgeService
from agent_bridge.web.pages import capability_admin_page


def create_app(paths: AgentBridgePaths | None = None, admins: set[str] | None = None) -> FastAPI:
    resolved_paths = paths or AgentBridgePaths.from_root()
    resolved_admins = admins if admins is not None else load_server_config(resolved_paths).admins
    service = AgentBridgeService.create(resolved_paths, resolved_admins)
    capability_schema_ready = False

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        service.store.init_schema()
        try:
            service.align_backends()
        except Exception:
            pass
        service.codegraph_scheduler.start()
        service.understand_scheduler.start()
        service.doc_sync_scheduler.start()
        service.workflow_scheduler.start()
        yield
        service.codegraph.ua_client.stop_all_dashboards()
        service.workflow_scheduler.stop()
        service.doc_sync_scheduler.stop()
        service.understand_scheduler.stop()
        service.codegraph_scheduler.stop()

    app = FastAPI(title="Agent Bridge", docs_url=None, openapi_url=None, redoc_url=None, lifespan=lifespan)
    app.state.agent_bridge_service = service
    app.add_middleware(
        DashboardProxyMiddleware,
        target_resolver=service.codegraph.dashboard_proxy_target,
        token_resolver=service.codegraph.dashboard_repo_by_token,
    )
    static_dir = Path(__file__).parent.parent / "static" / "capabilities"
    app.mount("/static/capabilities", StaticFiles(directory=static_dir, check_dir=False), name="capabilities-static")

    @app.get("/")
    def root() -> RedirectResponse:
        return RedirectResponse(url="/admin/capabilities", status_code=307)

    def actor(x_agent_bridge_user: str = Header(alias="X-Agent-Bridge-User")) -> str:
        return x_agent_bridge_user

    def call_safely(call: Callable[[], Any]) -> Any:
        try:
            if admins is None:
                service.admins = load_server_config(resolved_paths).admins
                service.capabilities.admins = service.admins
                service.governance.admins = service.admins
                service.codegraph.admins = service.admins
                service.workflows.admins = service.admins
                service.skills.admins = service.admins
                service.scripts.admins = service.admins
            return call()
        except AgentBridgeError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    async def call_safely_async(call: Callable[[], Awaitable[Any]]) -> Any:
        try:
            if admins is None:
                service.admins = load_server_config(resolved_paths).admins
                service.capabilities.admins = service.admins
                service.governance.admins = service.admins
                service.codegraph.admins = service.admins
                service.workflows.admins = service.admins
                service.skills.admins = service.admins
                service.scripts.admins = service.admins
            return await call()
        except AgentBridgeError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

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

    def ensure_capability_schema() -> None:
        nonlocal capability_schema_ready
        if not capability_schema_ready:
            service.store.init_schema()
            capability_schema_ready = True

    def catalog_sources(current_actor: str, profile_key: str | None, query: str | None) -> list[dict[str, Any]]:
        source_keys = [item["service_key"] for item in service.store.list_mcp_services()]
        allowed_keys = set(
            service.governance.filter_source_keys(
                actor=current_actor, profile_key=profile_key, source_type="mcp_service", source_keys=source_keys,
            )
        )
        sources = []
        for item in service.capabilities.list_services(current_actor):
            if item["service_key"] not in allowed_keys:
                continue
            tags = item.get("tags", [])
            text = f"{item['service_key']} {item['name']} {item.get('description', '')} {' '.join(tags)}".lower()
            if query and query.lower() not in text:
                continue
            sources.append({"source_type": "mcp_service", "source_key": item["service_key"], "name": item["name"], "description": item["description"], "status": item["status"], "tags": tags})
        return sources

    # Register route modules
    from agent_bridge.api.routes.health import router as health_router
    app.include_router(health_router)

    from agent_bridge.api.routes.knowledge import create_knowledge_routes
    app.include_router(create_knowledge_routes(service, actor, call_safely, save_upload, upload_filename))

    from agent_bridge.api.routes.capabilities import create_capability_routes
    app.include_router(create_capability_routes(service, actor, call_safely, call_safely_async, ensure_capability_schema, catalog_sources))

    from agent_bridge.api.routes.governance import create_governance_routes
    app.include_router(create_governance_routes(service, actor, call_safely, ensure_capability_schema))

    from agent_bridge.api.routes.builtins import create_builtin_routes
    app.include_router(create_builtin_routes(service, actor, call_safely, call_safely_async, ensure_capability_schema))

    from agent_bridge.api.routes.workflows import create_workflow_routes
    app.include_router(create_workflow_routes(service, actor, call_safely, ensure_capability_schema))

    # MCP streamable HTTP endpoint
    from agent_bridge.capabilities.mcp_server import setup_mcp_route
    setup_mcp_route(app, service)

    @app.get("/admin/capabilities", response_class=HTMLResponse)
    def capability_admin() -> HTMLResponse:
        return HTMLResponse(content=capability_admin_page(default_user()), media_type="text/html")

    return app
