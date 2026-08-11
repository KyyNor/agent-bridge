"""Internal script runtime helper endpoints."""
from __future__ import annotations

from contextlib import nullcontext
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from agent_bridge.api.runtime_context import profile_from_headers, workflow_context_from_headers
from agent_bridge.api.schemas import RuntimeWorkflowRunLogRequest, RuntimeWorkflowSetTaskRequest
from agent_bridge.automation.workflows.runtime_capability import WORKFLOW_CAPABILITY_HEADER


def create_script_runtime_routes(service, actor):
    router = APIRouter()

    def require_runtime_context(
        request: Request,
        current_actor: str,
    ) -> tuple[str | None, dict[str, str], str, Any]:
        profile_key = profile_from_headers(request)
        workflow_context = workflow_context_from_headers(request)
        if not workflow_context or not workflow_context.get("workflow_key") or not workflow_context.get("run_id"):
            raise HTTPException(status_code=400, detail="workflow context is required")
        current = {
            "workflow_key": str(workflow_context["workflow_key"]),
            "run_id": str(workflow_context["run_id"]),
        }
        capability_token = request.headers.get(WORKFLOW_CAPABILITY_HEADER, "").strip()
        if not capability_token:
            return profile_key, current, current_actor, nullcontext()
        capability = service.workflows.require_runtime_capability(
            capability_token,
            workflow_key=current["workflow_key"],
            run_id=current["run_id"],
            profile_key=profile_key,
        )
        return (
            capability.profile_key,
            current,
            capability.actor,
            service.workflows.bind_runtime_capability(capability),
        )

    @router.post("/runtime/workflow/get-task")
    def runtime_workflow_get_task(request: Request, current_actor: str = Depends(actor)) -> dict[str, Any]:
        profile_key, current, runtime_actor, runtime_scope = require_runtime_context(request, current_actor)
        with runtime_scope:
            return service.capabilities.invoke_logged_tool(
                actor=runtime_actor,
                profile_key=profile_key,
                entrypoint="runtime_workflow",
                source_type="builtin",
                source_key="workflow",
                tool_name="workflow_get_task",
                request={"workflow_key": current["workflow_key"], "run_id": current["run_id"]},
                handler=lambda: service.workflows.get_task_for_agent(
                    actor=runtime_actor,
                    profile_key=profile_key,
                    workflow_key=current["workflow_key"],
                    run_id=current["run_id"],
                ),
            )

    @router.post("/runtime/workflow/set-task")
    def runtime_workflow_set_task(
        payload: RuntimeWorkflowSetTaskRequest,
        request: Request,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        profile_key, current, runtime_actor, runtime_scope = require_runtime_context(request, current_actor)
        with runtime_scope:
            return service.capabilities.invoke_logged_tool(
                actor=runtime_actor,
                profile_key=profile_key,
                entrypoint="runtime_workflow",
                source_type="builtin",
                source_key="workflow",
                tool_name="workflow_set_task",
                request={"workflow_key": current["workflow_key"], "run_id": current["run_id"], "tasks": payload.tasks},
                handler=lambda: service.workflows.set_tasks_for_agent(
                    actor=runtime_actor,
                    profile_key=profile_key,
                    workflow_key=current["workflow_key"],
                    run_id=current["run_id"],
                    tasks=payload.tasks,
                ),
            )

    @router.post("/runtime/workflow/run-log")
    def runtime_workflow_run_log(
        payload: RuntimeWorkflowRunLogRequest,
        request: Request,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        profile_key, current, runtime_actor, runtime_scope = require_runtime_context(request, current_actor)
        with runtime_scope:
            return service.capabilities.invoke_logged_tool(
                actor=runtime_actor,
                profile_key=profile_key,
                entrypoint="runtime_workflow",
                source_type="builtin",
                source_key="workflow",
                tool_name="workflow_run_log",
                request={
                    "workflow_key": current["workflow_key"],
                    "run_id": current["run_id"],
                    "task_key": payload.task_key,
                    "level": payload.level,
                    "stage": payload.stage,
                    "message": payload.message,
                    "payload": payload.payload,
                },
                handler=lambda: _append_workflow_run_log(
                    service=service,
                    actor=runtime_actor,
                    profile_key=profile_key,
                    workflow_key=current["workflow_key"],
                    run_id=current["run_id"],
                    task_key=payload.task_key,
                    level=payload.level,
                    stage=payload.stage,
                    message=payload.message,
                    payload=payload.payload,
                ),
            )

    return router


def _append_workflow_run_log(
    *,
    service,
    actor: str,
    profile_key: str | None,
    workflow_key: str,
    run_id: str,
    task_key: str | None,
    level: str,
    stage: str,
    message: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    service.workflows.require_workflow_run_context(
        actor=actor,
        require_write=True,
        profile_key=profile_key,
        workflow_key=workflow_key,
        run_id=run_id,
    )
    service.workflows.append_run_log(
        actor=actor,
        workflow_key=workflow_key,
        run_id=run_id,
        task_key=task_key,
        level=level,
        stage=stage,
        message=message,
        payload=payload,
    )
    return {"ok": True}
