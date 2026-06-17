"""Workflow definition, run log, and artifact endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from agent_bridge.api.schemas import WorkflowDefinitionRequest


def create_workflow_routes(service, actor, call_safely, ensure_capability_schema):
    router = APIRouter()

    @router.get("/workflows")
    def list_workflows(current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        ensure_capability_schema()
        return call_safely(lambda: service.workflows.list_definitions(current_actor))

    @router.post("/workflows")
    def upsert_workflow(payload: WorkflowDefinitionRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.workflows.upsert_definition(actor=current_actor, **payload.model_dump()))

    @router.get("/workflows/{workflow_key}")
    def get_workflow(workflow_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.workflows.get_definition(current_actor, workflow_key))

    @router.post("/workflows/{workflow_key}/delete")
    def delete_workflow(workflow_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(lambda: service.workflows.delete_definition(current_actor, workflow_key))

    @router.get("/workflows/{workflow_key}/runs")
    def list_workflow_runs(
        workflow_key: str,
        limit: int = 20,
        current_actor: str = Depends(actor),
    ) -> list[dict[str, Any]]:
        ensure_capability_schema()
        return call_safely(lambda: service.workflows.list_runs(current_actor, workflow_key, limit=limit))

    @router.get("/workflow-runs/{run_id}/logs")
    def list_run_logs(run_id: str, current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        ensure_capability_schema()
        return call_safely(lambda: service.workflows.list_run_logs(current_actor, run_id))

    @router.get("/workflow-artifacts")
    def search_artifacts(
        profile_key: str | None = None,
        query: str | None = None,
        path: str | None = None,
        workflow_key: str | None = None,
        tags: list[str] = Query(default=[]),
        limit: int = 20,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(
            lambda: service.workflows.search_artifacts(
                actor=current_actor,
                profile_key=profile_key,
                query=query,
                tags=tags,
                path=path,
                workflow_key=workflow_key,
                limit=limit,
            )
        )

    @router.get("/workflow-artifacts/{artifact_id}")
    def get_artifact(
        artifact_id: str,
        profile_key: str | None = None,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        ensure_capability_schema()
        return call_safely(
            lambda: service.workflows.get_artifact(
                actor=current_actor,
                artifact_id=artifact_id,
                profile_key=profile_key,
            )
        )

    return router
