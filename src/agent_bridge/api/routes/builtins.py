"""Code repository and sync management endpoints."""
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from agent_bridge.api.schemas import CodeRepoCategoryRequest, CodeRepositoryRequest, KnowledgeSyncConfigRequest


class _CodeGraphQueryRequest(BaseModel):
    query: str
    limit: int = 20


def create_builtin_routes(service, actor, call_safely, call_safely_async, ensure_capability_schema):
    router = APIRouter()

    # -- Code Repositories --

    @router.get("/code-repo/repositories")
    def list_code_repositories(current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        ensure_capability_schema()
        return call_safely(lambda: service.codegraph.list_repositories(current_actor))

    @router.post("/code-repo/repositories")
    def upsert_code_repository(payload: CodeRepositoryRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.codegraph.upsert_repository(current_actor, **payload.model_dump()))

    @router.get("/code-repo/status")
    def get_codegraph_status(current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.codegraph.get_status(current_actor))

    @router.post("/code-repo/repositories/{repo_key}/sync")
    def sync_code_repository(repo_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.codegraph.sync_repository(current_actor, repo_key))

    @router.get("/code-repo/repositories/{repo_key}/overview")
    def get_repo_overview(repo_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.codegraph.repository_overview(current_actor, repo_key))

    @router.get("/code-repo/repositories/{repo_key}/files")
    def list_repo_files(repo_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: {"files": service.codegraph.list_files(current_actor, repo_key)})

    @router.post("/code-repo/repositories/{repo_key}/query")
    def query_repo(repo_key: str, payload: _CodeGraphQueryRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: {"matches": service.codegraph.search_code(current_actor, repo_key, query=payload.query, limit=payload.limit)})

    @router.post("/code-repo/repositories/{repo_key}/explore")
    async def explore_repo(repo_key: str, payload: _CodeGraphQueryRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return await call_safely_async(lambda: service.codegraph.explore(current_actor, repo_key, query=payload.query))

    @router.post("/code-repo/repositories/{repo_key}/callers")
    def find_callers(repo_key: str, payload: _CodeGraphQueryRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: {"matches": service.codegraph.callers(current_actor, repo_key, symbol=payload.query, limit=payload.limit)})

    @router.post("/code-repo/repositories/{repo_key}/callees")
    def find_callees(repo_key: str, payload: _CodeGraphQueryRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: {"matches": service.codegraph.callees(current_actor, repo_key, symbol=payload.query, limit=payload.limit)})

    @router.post("/code-repo/repositories/{repo_key}/impact")
    def analyze_impact(repo_key: str, payload: _CodeGraphQueryRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: {"matches": service.codegraph.impact(current_actor, repo_key, symbol=payload.query)})

    # -- Understand Anything --

    @router.get("/code-repo/repositories/{repo_key}/understand/status")
    def get_understand_status(repo_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.codegraph.get_understand_status(current_actor, repo_key))

    @router.get("/code-repo/repositories/{repo_key}/understand/summary")
    def get_understand_summary(repo_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        result = call_safely(lambda: service.codegraph.get_understand_summary(current_actor, repo_key))
        if result is None:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=404, content={"error": "understand_graph_not_found"})
        return result

    # -- Categories --

    @router.get("/code-repo/categories")
    def list_categories(current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        ensure_capability_schema()
        return call_safely(lambda: service.list_categories(current_actor))

    @router.post("/code-repo/categories")
    def upsert_category(payload: CodeRepoCategoryRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.upsert_category(current_actor, **payload.model_dump()))

    @router.post("/code-repo/categories/{category_key}/delete")
    def delete_category(category_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        call_safely(lambda: service.delete_category(current_actor, category_key))
        return {"ok": True}

    # -- Sync Config --

    @router.get("/sync-config")
    def get_sync_config(current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.get_sync_config(current_actor))

    @router.post("/sync-config")
    def save_sync_config(payload: KnowledgeSyncConfigRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.save_sync_config(current_actor, **payload.model_dump()))

    @router.get("/sync-config/scheduler-status")
    def get_scheduler_status(current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.get_scheduler_status(current_actor))

    return router
