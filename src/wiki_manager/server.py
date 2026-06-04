from __future__ import annotations

import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from wiki_manager.config import DEFAULT_ROOT, WikiManagerPaths, load_server_config
from wiki_manager.domain import KbRole, WikiManagerError
from wiki_manager.services import WikiManagerService
from wiki_manager.web_pages import capability_admin_page


class CreateKbRequest(BaseModel):
    slug: str
    name: str
    description: str = ""


class GrantMemberRequest(BaseModel):
    linux_user: str
    role: KbRole


class SyncRequest(BaseModel):
    all_users: bool = False


class AskRequest(BaseModel):
    kb: str
    question: str
    backend: str | None = None
    session_id: str | None = None


class PurgeRequest(BaseModel):
    confirm: bool = False


class RegisterMcpServiceRequest(BaseModel):
    service_key: str
    name: str
    endpoint_url: str
    headers: dict[str, Any] | None = None
    description: str = ""
    tags: list[str] = Field(default_factory=list)


class UpdateMcpServiceStatusRequest(BaseModel):
    status: str


class ProjectProfileRequest(BaseModel):
    profile_key: str
    name: str
    description: str = ""
    status: str = "active"


class ProfileSourceRuleRequest(BaseModel):
    source_type: str
    source_key: str
    effect: str


class ProfileRulesRequest(BaseModel):
    rules: list[ProfileSourceRuleRequest] = Field(default_factory=list)


class MetaMcpSearchRequest(BaseModel):
    path: str | None = None
    query: str | None = None
    limit: int = 20


class MetaMcpExecuteRequest(BaseModel):
    service: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


def create_app(paths: WikiManagerPaths | None = None, admins: set[str] | None = None) -> FastAPI:
    resolved_paths = paths or WikiManagerPaths.from_root(DEFAULT_ROOT)
    resolved_admins = admins if admins is not None else load_server_config(resolved_paths).admins
    service = WikiManagerService.create(resolved_paths, resolved_admins)
    capability_schema_ready = False

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        service.align_backends()
        yield

    app = FastAPI(title="wiki-manager", docs_url=None, openapi_url=None, redoc_url=None, lifespan=lifespan)
    static_dir = Path(__file__).parent / "static" / "capabilities"
    app.mount("/static/capabilities", StaticFiles(directory=static_dir), name="capabilities-static")

    def actor(x_wiki_user: str = Header(alias="X-Wiki-User")) -> str:
        return x_wiki_user

    def metamcp_profile(
        x_wiki_metamcp_profile: str | None = Header(default=None, alias="X-Wiki-MetaMCP-Profile"),
    ) -> str | None:
        return x_wiki_metamcp_profile

    def call_safely(call: Callable[[], Any]) -> Any:
        try:
            if admins is None:
                service.admins = load_server_config(resolved_paths).admins
                service.capabilities.admins = service.admins
                service.governance.admins = service.admins
            return call()
        except WikiManagerError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    async def call_safely_async(call: Callable[[], Awaitable[Any]]) -> Any:
        try:
            if admins is None:
                service.admins = load_server_config(resolved_paths).admins
                service.capabilities.admins = service.admins
                service.governance.admins = service.admins
            return await call()
        except WikiManagerError as exc:
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
                actor=current_actor,
                profile_key=profile_key,
                source_type="mcp_service",
                source_keys=source_keys,
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
            sources.append(
                {
                    "source_type": "mcp_service",
                    "source_key": item["service_key"],
                    "name": item["name"],
                    "description": item["description"],
                    "status": item["status"],
                    "tags": tags,
                }
            )
        return sources

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/backends")
    def list_backends() -> list[dict[str, str]]:
        if service.registry is None:
            return []
        result = []
        for slug in service.registry.list_slugs():
            adapter = service.registry.get(slug)
            module = type(adapter).__module__
            backend_type = "ragflow" if "ragflow" in module else "mock"
            result.append({"slug": slug, "type": backend_type, "status": "active"})
        return result

    @app.post("/admin/init")
    def init_system(current_actor: str = Depends(actor)) -> None:
        return call_safely(lambda: service.init_system())

    @app.post("/kbs")
    def create_kb(payload: CreateKbRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return call_safely(lambda: service.create_kb(current_actor, payload.slug, payload.name, payload.description))

    @app.get("/kbs")
    def list_kbs(current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        return call_safely(lambda: service.list_kbs(current_actor))

    @app.post("/kbs/{kb_slug}/members")
    def grant_kb_member(
        kb_slug: str,
        payload: GrantMemberRequest,
        current_actor: str = Depends(actor),
    ) -> dict[str, str]:
        return call_safely(lambda: service.grant_kb_member(current_actor, kb_slug, payload.linux_user, payload.role))

    @app.get("/kbs/{kb_slug}/members")
    def list_kb_members(kb_slug: str, current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        return call_safely(lambda: service.list_kb_members(current_actor, kb_slug))

    @app.post("/docs")
    def add_document(
        current_actor: str = Depends(actor),
        file: UploadFile = File(),
        kb: list[str] = Form(),
        later: bool = Form(False),
    ) -> dict[str, Any]:
        upload_path = save_upload(file)
        try:
            return call_safely(
                lambda: service.add_document(
                    current_actor,
                    upload_path,
                    kb,
                    later,
                    original_filename=upload_filename(file),
                )
            )
        finally:
            upload_path.unlink(missing_ok=True)

    @app.get("/docs")
    def list_docs(kb: str, backend: str | None = None, current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        return call_safely(lambda: service.list_docs(current_actor, kb, backend=backend))

    @app.get("/docs/{doc_slug}")
    def get_doc(doc_slug: str, backend: str | None = None, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return call_safely(lambda: service.get_doc(current_actor, doc_slug, backend=backend))

    @app.post("/docs/{doc_slug}/versions")
    def update_document(
        doc_slug: str,
        current_actor: str = Depends(actor),
        file: UploadFile = File(),
        later: bool = Form(False),
    ) -> dict[str, Any]:
        upload_path = save_upload(file)
        try:
            return call_safely(
                lambda: service.update_document(
                    current_actor,
                    doc_slug,
                    upload_path,
                    later,
                    original_filename=upload_filename(file),
                )
            )
        finally:
            upload_path.unlink(missing_ok=True)

    @app.post("/docs/{doc_slug}/delete")
    def delete_document(doc_slug: str, current_actor: str = Depends(actor)) -> dict[str, str]:
        return call_safely(lambda: service.delete_document(current_actor, doc_slug))

    @app.post("/docs/{doc_slug}/purge")
    def purge_document(
        doc_slug: str,
        payload: PurgeRequest,
        current_actor: str = Depends(actor),
    ) -> dict[str, str]:
        return call_safely(lambda: service.purge_document(current_actor, doc_slug, confirm=payload.confirm))

    @app.get("/status")
    def status(backend: str | None = None, current_actor: str = Depends(actor)) -> dict[str, list[dict[str, Any]]]:
        return call_safely(lambda: service.status(current_actor, backend=backend))

    @app.post("/sync")
    def sync(payload: SyncRequest, backend: str | None = None, current_actor: str = Depends(actor)) -> dict[str, int]:
        return call_safely(lambda: service.sync(current_actor, all_users=payload.all_users, backend=backend))

    @app.get("/search")
    def search(q: str, kb: str, backend: str | None = None, top_k: int = 6, current_actor: str = Depends(actor)) -> dict[str, Any]:
        results = call_safely(
            lambda: service.search(current_actor, kb, q, backend_slug=backend, top_k=top_k)
        )
        return {"results": [{"chunk_id": r.chunk_id, "content": r.content, "document_name": r.document_name, "similarity": r.similarity, "dataset_id": r.dataset_id} for r in results]}

    @app.post("/ask")
    def ask(payload: AskRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        result = call_safely(
            lambda: service.ask(current_actor, payload.kb, payload.question, backend_slug=payload.backend, session_id=payload.session_id)
        )
        return {
            "answer": result.answer,
            "chunks": [{"chunk_id": c.chunk_id, "content": c.content, "document_name": c.document_name, "similarity": c.similarity, "dataset_id": c.dataset_id} for c in result.chunks],
            "session_id": result.session_id,
        }

    @app.post("/capabilities/mcp-services")
    def register_mcp_service(
        payload: RegisterMcpServiceRequest,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(
            lambda: service.capabilities.register_service(
                current_actor,
                payload.service_key,
                payload.name,
                payload.endpoint_url,
                payload.headers,
                payload.description,
                payload.tags,
            )
        )

    @app.get("/capabilities/mcp-services")
    def list_mcp_services(current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        ensure_capability_schema()
        return call_safely(lambda: service.capabilities.list_services(current_actor))

    @app.post("/capabilities/mcp-services/{service_key}/status")
    def update_mcp_service_status(
        service_key: str,
        payload: UpdateMcpServiceStatusRequest,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.capabilities.set_service_status(current_actor, service_key, payload.status))

    @app.post("/capabilities/mcp-services/{service_key}/sync")
    async def sync_mcp_service_tools(service_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return await call_safely_async(lambda: service.capabilities.sync_tools(current_actor, service_key))

    @app.get("/capabilities/mcp-services/{service_key}/tools")
    def list_mcp_service_tools(service_key: str, current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        ensure_capability_schema()
        return call_safely(lambda: service.capabilities.list_tools(current_actor, service_key))

    @app.post("/capability-profiles")
    def upsert_capability_profile(
        payload: ProjectProfileRequest,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(
            lambda: service.governance.upsert_profile(
                current_actor,
                payload.profile_key,
                payload.name,
                payload.description,
                payload.status,
            )
        )

    @app.get("/capability-profiles")
    def list_capability_profiles(current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        ensure_capability_schema()
        return call_safely(lambda: service.governance.list_profiles(current_actor))

    @app.get("/capability-profiles/{profile_key}")
    def get_capability_profile(profile_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.governance.get_profile(current_actor, profile_key))

    @app.put("/capability-profiles/{profile_key}/rules")
    def replace_capability_profile_rules(
        profile_key: str,
        payload: ProfileRulesRequest,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        ensure_capability_schema()
        rules = [rule.model_dump() for rule in payload.rules]
        return call_safely(lambda: service.governance.replace_profile_rules(current_actor, profile_key, rules))

    @app.get("/tool-call-logs")
    def list_tool_call_logs(
        entrypoint: str | None = None,
        source_type: str | None = None,
        source_key: str | None = None,
        tool_name: str | None = None,
        profile_key: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
        current_actor: str = Depends(actor),
    ) -> list[dict[str, Any]]:
        ensure_capability_schema()
        return call_safely(
            lambda: service.governance.list_logs(
                actor=current_actor,
                entrypoint=entrypoint,
                source_type=source_type,
                source_key=source_key,
                tool_name=tool_name,
                profile_key=profile_key,
                status=status,
                limit=limit,
                offset=offset,
            )
        )

    @app.get("/tool-call-logs/{log_id}")
    def get_tool_call_log(log_id: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.governance.get_log(actor=current_actor, log_id=log_id))

    @app.get("/capability-catalog")
    def capability_catalog(
        profile_key: str | None = None,
        query: str | None = None,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: {"sources": catalog_sources(current_actor, profile_key, query)})

    @app.get("/capability-catalog/sources/{source_type}/{source_key}")
    def capability_source_detail(source_type: str, source_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        if source_type != "mcp_service":
            raise HTTPException(status_code=404, detail="source not found")
        service_payload = call_safely(lambda: service.capabilities.get_service(current_actor, source_key))
        tools = call_safely(lambda: service.capabilities.list_tools(current_actor, source_key))
        return {"source_type": source_type, "source": service_payload, "tools": tools}

    @app.get("/capability-catalog/sources/{source_type}/{source_key}/tools/{tool_name}")
    def capability_tool_detail(
        source_type: str,
        source_key: str,
        tool_name: str,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        ensure_capability_schema()
        if source_type != "mcp_service":
            raise HTTPException(status_code=404, detail="tool not found")
        tools = call_safely(lambda: service.capabilities.list_tools(current_actor, source_key))
        for tool in tools:
            if tool["tool"] == tool_name:
                logs = call_safely(
                    lambda: service.governance.list_logs(
                        actor=current_actor,
                        source_type=source_type,
                        source_key=source_key,
                        tool_name=tool_name,
                        limit=10,
                    )
                )
                return {"source_type": source_type, "source_key": source_key, "tool": tool, "logs": logs}
        raise HTTPException(status_code=404, detail="tool not found")

    @app.post("/mcp/search")
    def metamcp_search(
        payload: MetaMcpSearchRequest,
        current_actor: str = Depends(actor),
        profile_key: str | None = Depends(metamcp_profile),
    ) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(
            lambda: service.capabilities.search(
                current_actor,
                payload.path,
                payload.query,
                payload.limit,
                profile_key=profile_key,
            )
        )

    @app.post("/mcp/execute")
    async def metamcp_execute(
        payload: MetaMcpExecuteRequest,
        current_actor: str = Depends(actor),
        profile_key: str | None = Depends(metamcp_profile),
    ) -> dict[str, Any]:
        ensure_capability_schema()
        return await call_safely_async(
            lambda: service.capabilities.execute(
                current_actor,
                payload.service,
                payload.tool,
                payload.arguments,
                profile_key=profile_key,
            )
        )

    @app.get("/admin/capabilities", response_class=HTMLResponse)
    def capability_admin() -> HTMLResponse:
        return HTMLResponse(content=capability_admin_page(), media_type="text/html")

    return app
