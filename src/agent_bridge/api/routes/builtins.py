"""Built-in Wiki and CodeGraph resource management endpoints."""
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from agent_bridge.api.schemas import CodeRepositoryRequest


class CodeGraphQueryRequest(BaseModel):
    query: str
    limit: int = 20


def create_builtin_routes(service, actor, call_safely, call_safely_async, ensure_capability_schema):
    router = APIRouter()

    @router.get("/builtin/codegraph/repositories")
    def list_code_repositories(current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        ensure_capability_schema()
        return call_safely(lambda: service.codegraph.list_repositories(current_actor))

    @router.post("/builtin/codegraph/repositories")
    def upsert_code_repository(payload: CodeRepositoryRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.codegraph.upsert_repository(current_actor, **payload.model_dump()))

    @router.get("/builtin/codegraph/status")
    def get_codegraph_status(current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.codegraph.get_status(current_actor))

    @router.post("/builtin/codegraph/repositories/{repo_key}/sync")
    def sync_code_repository(repo_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.codegraph.sync_repository(current_actor, repo_key))

    @router.get("/builtin/codegraph/repositories/{repo_key}/overview")
    def get_repo_overview(repo_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.codegraph.repository_overview(current_actor, repo_key))

    @router.get("/builtin/codegraph/repositories/{repo_key}/files")
    def list_repo_files(repo_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: {"files": service.codegraph.list_files(current_actor, repo_key)})

    @router.post("/builtin/codegraph/repositories/{repo_key}/query")
    def query_repo(repo_key: str, payload: CodeGraphQueryRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: {"matches": service.codegraph.search_code(current_actor, repo_key, query=payload.query, limit=payload.limit)})

    @router.post("/builtin/codegraph/repositories/{repo_key}/explore")
    async def explore_repo(repo_key: str, payload: CodeGraphQueryRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return await call_safely_async(lambda: service.codegraph.explore(current_actor, repo_key, query=payload.query))

    @router.post("/builtin/codegraph/repositories/{repo_key}/callers")
    def find_callers(repo_key: str, payload: CodeGraphQueryRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: {"matches": service.codegraph.callers(current_actor, repo_key, symbol=payload.query, limit=payload.limit)})

    @router.post("/builtin/codegraph/repositories/{repo_key}/callees")
    def find_callees(repo_key: str, payload: CodeGraphQueryRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: {"matches": service.codegraph.callees(current_actor, repo_key, symbol=payload.query, limit=payload.limit)})

    @router.post("/builtin/codegraph/repositories/{repo_key}/impact")
    def analyze_impact(repo_key: str, payload: CodeGraphQueryRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: {"matches": service.codegraph.impact(current_actor, repo_key, symbol=payload.query)})

    return router
