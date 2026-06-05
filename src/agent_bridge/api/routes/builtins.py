"""Built-in Wiki and CodeGraph resource management endpoints."""
from typing import Any

from fastapi import APIRouter, Depends

from agent_bridge.api.schemas import CodeRepositoryRequest




def create_builtin_routes(service, actor, call_safely, ensure_capability_schema):
    router = APIRouter()

    @router.get("/builtin/codegraph/repositories")
    def list_code_repositories(current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        ensure_capability_schema()
        return call_safely(lambda: service.codegraph.list_repositories(current_actor))

    @router.post("/builtin/codegraph/repositories")
    def upsert_code_repository(payload: CodeRepositoryRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.codegraph.upsert_repository(current_actor, **payload.model_dump()))

    @router.post("/builtin/codegraph/repositories/{repo_key}/sync")
    def sync_code_repository(repo_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.codegraph.sync_repository(current_actor, repo_key))

    return router
