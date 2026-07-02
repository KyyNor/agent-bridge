"""Workflow definition, run log, and artifact endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from agent_bridge.api.schemas import WorkflowDefinitionRequest


def create_workflow_routes(service, actor):
    router = APIRouter()

    @router.get("/workflows")
    def list_workflows(current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        return service.workflows.list_definitions(current_actor)

    @router.post("/workflows")
    def upsert_workflow(payload: WorkflowDefinitionRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.workflows.upsert_definition(actor=current_actor, **payload.model_dump())

    @router.get("/workflows/{workflow_key}")
    def get_workflow(workflow_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.workflows.get_definition(current_actor, workflow_key)

    @router.post("/workflows/{workflow_key}/delete")
    def delete_workflow(workflow_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.workflows.delete_definition(current_actor, workflow_key)

    @router.post("/workflows/{workflow_key}/clear")
    def clear_workflow_execution_data(workflow_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.workflows.clear_execution_data(current_actor, workflow_key)

    @router.get("/workflows/{workflow_key}/runs")
    def list_workflow_runs(
        workflow_key: str,
        limit: int = 200,
        current_actor: str = Depends(actor),
    ) -> list[dict[str, Any]]:
        return service.workflows.list_runs(current_actor, workflow_key, limit=limit)

    @router.get("/workflows/{workflow_key}/tasks")
    def list_workflow_tasks(
        workflow_key: str,
        status: str | None = None,
        type: str | None = None,
        search: str | None = None,
        sort: str | None = None,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        return service.workflows.list_tasks(
            current_actor,
            workflow_key,
            status=status,
            type=type,
            search=search,
            sort=sort,
        )

    @router.get("/workflow-runs/{run_id}/logs")
    def list_run_logs(run_id: str, current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        return service.workflows.list_run_logs(current_actor, run_id)

    @router.get("/workflow-runs/{run_id}/events")
    def list_run_events(run_id: str, current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        return service.workflows.list_run_events(current_actor, run_id)

    @router.get("/workflow-runs/{run_id}/subagent-detail")
    def get_run_subagent_detail(
        run_id: str,
        task_id: str,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        return service.workflows.get_run_subagent_detail(current_actor, run_id, task_id)

    @router.get("/workflow-artifacts")
    def search_artifacts(
        profile_key: str | None = None,
        query: str | None = None,
        path: str | None = None,
        workflow_key: str | None = None,
        task_key: str | None = None,
        task_version: str | None = None,
        include_history: bool = False,
        full: bool = False,
        tags: list[str] = Query(default=[]),
        limit: int = 20,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        return service.workflows.search_artifacts(
            actor=current_actor,
            profile_key=profile_key,
            query=query,
            tags=tags,
            path=path,
            workflow_key=workflow_key,
            task_key=task_key,
            task_version=task_version,
            include_history=include_history,
            full=full,
            limit=limit,
        )

    @router.get("/workflow-artifacts/history")
    def list_artifact_history(
        profile_key: str | None = None,
        workflow_key: str = "",
        task_key: str = "",
        limit: int = 20,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        return service.workflows.list_artifact_history(
            actor=current_actor,
            profile_key=profile_key,
            workflow_key=workflow_key,
            task_key=task_key,
            limit=limit,
        )

    @router.get("/workflow-artifacts/{artifact_id}")
    def get_artifact(
        artifact_id: str,
        profile_key: str | None = None,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        return service.workflows.get_artifact(
            actor=current_actor,
            artifact_id=artifact_id,
            profile_key=profile_key,
        )

    @router.post("/workflows/{workflow_key}/run")
    def run_workflow(workflow_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.workflow_scheduler.run_workflow_now(workflow_key)

    @router.post("/workflows/{workflow_key}/tasks/{task_key:path}/execute")
    def execute_task(
        workflow_key: str,
        task_key: str,
        task_version: str | None = None,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        # Stamp the one-shot priority flag, then start a workflow run. The
        # flagged task is leased first by the agent's workflow_get_task call.
        flagged = service.workflows.execute_task(
            actor=current_actor,
            workflow_key=workflow_key,
            task_key=task_key,
            task_version=task_version,
        )
        started = service.workflow_scheduler.run_workflow_now(workflow_key)
        return {**flagged, "run_id": started.get("run_id"), "run_status": started.get("status")}

    @router.post("/workflows/{workflow_key}/tasks/{task_key:path}/reset")
    def reset_task(
        workflow_key: str,
        task_key: str,
        task_version: str | None = None,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        return service.workflows.reset_task(
            actor=current_actor,
            workflow_key=workflow_key,
            task_key=task_key,
            task_version=task_version,
        )

    @router.get("/workflow-runs/{run_id}")
    def get_run(run_id: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.workflows.get_run(current_actor, run_id)

    return router
