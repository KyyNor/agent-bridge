"""Code repository and sync management endpoints."""
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from agent_bridge.api.runtime_context import profile_from_headers, workflow_context_from_headers
from agent_bridge.api.schemas import (
    CodeRepoCategoryRequest,
    CodeRepositoryRequest,
    KnowledgeSyncConfigRequest,
    ScriptRequest,
    ScriptTestRunRequest,
    SkillPromptRequest,
)


class _CodeGraphQueryRequest(BaseModel):
    query: str
    limit: int = 20


class _TestCloneRequest(BaseModel):
    git_url: str
    auth_ref: str = ""


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

    @router.post("/code-repo/test-clone")
    def test_clone(payload: _TestCloneRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.codegraph.test_clone(current_actor, payload.git_url, payload.auth_ref))

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

    @router.get("/code-repo/repositories/{repo_key}/understand/availability")
    def check_understand_availability(repo_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.codegraph.check_understand_availability(current_actor, repo_key))

    @router.post("/code-repo/repositories/{repo_key}/understand/analyze")
    def trigger_understand_analyze(repo_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.codegraph.analyze_understand(current_actor, repo_key))

    @router.get("/code-repo/repositories/{repo_key}/understand/dashboard")
    def dashboard_status(repo_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.codegraph.dashboard_status_understand(current_actor, repo_key))

    @router.post("/code-repo/repositories/{repo_key}/understand/dashboard/start")
    def start_dashboard(repo_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.codegraph.start_dashboard_understand(current_actor, repo_key))

    @router.post("/code-repo/repositories/{repo_key}/understand/dashboard/stop")
    def stop_dashboard(repo_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.codegraph.stop_dashboard_understand(current_actor, repo_key))

    @router.post("/code-repo/repositories/{repo_key}/understand/dashboard/touch")
    def touch_dashboard(repo_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.codegraph.touch_understand_dashboard(current_actor, repo_key))

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

    # -- Skills --

    @router.get("/skills")
    def list_skills(current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        ensure_capability_schema()
        return call_safely(lambda: service.skills.list_skills(current_actor))

    @router.get("/skills/{skill_name}")
    def get_skill(skill_name: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.skills.get_skill(current_actor, skill_name))

    @router.post("/skills/{skill_name}")
    def save_skill(
        skill_name: str,
        payload: SkillPromptRequest,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.skills.save_skill(current_actor, skill_name, payload.prompt))

    @router.post("/skills/{skill_name}/reset")
    def reset_skill(skill_name: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.skills.reset_skill(current_actor, skill_name))

    # -- Scripts --

    @router.get("/scripts")
    def list_scripts(current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        ensure_capability_schema()
        return call_safely(lambda: service.scripts.list_scripts(current_actor))

    @router.post("/scripts")
    def upsert_script(payload: ScriptRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.scripts.upsert_script(actor=current_actor, **payload.model_dump()))

    @router.get("/scripts/{script_key}")
    def get_script(script_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.scripts.get_script(current_actor, script_key))

    @router.post("/scripts/{script_key}/delete")
    def delete_script(script_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.scripts.delete_script(current_actor, script_key))

    @router.post("/scripts/{script_key}/test")
    def test_script(
        script_key: str,
        payload: ScriptTestRunRequest,
        request: Request,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(
            lambda: service.scripts.test_script(
                actor=current_actor,
                script_key=script_key,
                script_params=payload.script_params,
                timeout_seconds=payload.timeout_seconds,
                profile_key=profile_from_headers(request),
                workflow_context=workflow_context_from_headers(request),
            )
        )

    @router.get("/scripts/{script_key}/runs")
    def list_script_runs(
        script_key: str,
        limit: int = 20,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.scripts.list_runs(current_actor, script_key, limit=limit))

    @router.get("/script-runs/{run_id}")
    def get_script_run(run_id: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.scripts.get_run(current_actor, run_id))

    return router
