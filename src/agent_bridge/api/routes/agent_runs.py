"""Agent run log endpoints: list and inspect AgentService invocations."""
from typing import Any

from fastapi import APIRouter, Depends


def create_agent_runs_routes(service, actor, call_safely, ensure_capability_schema):
    router = APIRouter()

    @router.get("/agent-runs")
    def list_agent_runs(
        agent_name: str | None = None,
        profile_key: str | None = None,
        workflow_key: str | None = None,
        workflow_run_id: str | None = None,
        ok: bool | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
        limit: int = 50,
        offset: int = 0,
        current_actor: str = Depends(actor),
    ) -> list[dict[str, Any]]:
        ensure_capability_schema()
        return call_safely(
            lambda: service.store.agent_runs.list(
                agent_name=agent_name,
                profile_key=profile_key,
                workflow_key=workflow_key,
                workflow_run_id=workflow_run_id,
                ok=ok,
                created_from=created_from,
                created_to=created_to,
                limit=limit,
                offset=offset,
            )
        )

    @router.get("/agent-runs/{run_key}")
    def get_agent_run(run_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        from agent_bridge.core.domain import NotFound

        def _get() -> dict[str, Any]:
            row = service.store.agent_runs.get(run_key)
            if row is None:
                raise NotFound("agent run not found")
            return row

        return call_safely(_get)

    return router
