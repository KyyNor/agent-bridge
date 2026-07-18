"""Workflow definition, run log, and artifact endpoints."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile

from agent_bridge.api.schemas import (
    WorkflowDefinitionRequest,
    WorkflowImportConfirmRequest,
    WorkflowRunRequest,
    WorkflowRunPreviewRequest,
    WorkflowTaskImportConfirmRequest,
    WorkflowValidationRequest,
)
from agent_bridge.core.domain import require_admin_user


def create_workflow_routes(service, actor):
    router = APIRouter()

    @router.get("/workflows")
    def list_workflows(current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        return service.workflows.list_definitions(current_actor)

    @router.post("/workflows")
    def upsert_workflow(payload: WorkflowDefinitionRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.workflows.upsert_definition(actor=current_actor, **payload.model_dump())

    @router.post("/workflows/validate")
    def validate_workflow(payload: WorkflowValidationRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.validate_workflow_draft(actor=current_actor, workflow=payload.workflow)

    @router.get("/workflows/{workflow_key}")
    def get_workflow(workflow_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.workflows.get_definition(current_actor, workflow_key)

    @router.post("/workflows/{workflow_key}/delete")
    def delete_workflow(workflow_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.workflows.delete_definition(current_actor, workflow_key)

    @router.post("/workflows/{workflow_key}/clear")
    def clear_workflow_execution_data(workflow_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.workflows.clear_execution_data(current_actor, workflow_key)

    @router.get("/workflows/{workflow_key}/revisions")
    def list_workflow_revisions(
        workflow_key: str, limit: int = 100, current_actor: str = Depends(actor)
    ) -> list[dict[str, Any]]:
        return service.workflows.list_revisions(current_actor, workflow_key, limit=limit)

    @router.get("/workflows/{workflow_key}/revisions/{revision_no}")
    def get_workflow_revision(
        workflow_key: str, revision_no: int, current_actor: str = Depends(actor)
    ) -> dict[str, Any]:
        return service.workflows.get_revision(current_actor, workflow_key, revision_no)

    @router.post("/workflows/{workflow_key}/revisions/{revision_no}/restore")
    def restore_workflow_revision(
        workflow_key: str, revision_no: int, current_actor: str = Depends(actor)
    ) -> dict[str, Any]:
        return service.workflows.restore_revision(current_actor, workflow_key, revision_no)

    @router.get("/workflows/{workflow_key}/diff")
    def diff_workflow(
        workflow_key: str,
        current_actor: str = Depends(actor),
        from_revision: int | None = None,
        to_revision: int | None = None,
    ) -> dict[str, Any]:
        return service.workflows.diff_revisions(
            current_actor, workflow_key, from_no=from_revision, to_no=to_revision
        )

    @router.get("/workflows/{workflow_key}/export")
    def export_workflow(workflow_key: str, current_actor: str = Depends(actor)) -> Response:
        payload = service.workflows.export_definition(current_actor, workflow_key)
        return Response(
            content=json.dumps(payload, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="{workflow_key}.workflow.json"'
            },
        )

    @router.post("/workflows/import/preview")
    async def preview_workflow_import(
        file: UploadFile = File(description="workflow export JSON"),
        target_workflow_key: str | None = Form(None),
        target_mode: str = Form("auto"),
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        return service.workflows.preview_definition_import(
            actor=current_actor,
            filename=file.filename or "workflow.workflow.json",
            content=await file.read(),
            target_workflow_key=target_workflow_key,
            target_mode=target_mode,
        )

    @router.post("/workflows/import/confirm")
    def confirm_workflow_import(
        payload: WorkflowImportConfirmRequest,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        return service.workflows.confirm_definition_import(current_actor, payload.import_id)

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
        # Structured workflow stage logs (workflow_run_logs table), written by
        # the workflow helper tools. This is workflow-scheduling-specific and
        # stays here; agent execution events/subagent-detail live under
        # /agent-runs (unified across all agent runs).
        return service.workflows.list_run_logs(current_actor, run_id)

    @router.post("/workflow-runs/{run_id}/stop")
    def stop_workflow_run(
        run_id: str,
        response: Response,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        require_admin_user(current_actor, service.admins)
        result = service.workflow_scheduler.stop_workflow_run(run_id)
        if result.get("status") == "stopping":
            response.status_code = 202
        return result

    @router.get("/workflow-artifacts")
    def search_artifacts(
        profile_key: str | None = None,
        query: str | None = None,
        path: str | None = None,
        workflow_key: str | None = None,
        task_key: str | None = None,
        task_version: str | None = None,
        run_id: str | None = None,
        include_history: bool = False,
        full: bool = False,
        tags: list[str] = Query(default=[]),
        format: str | None = None,
        limit: int = 20,
        offset: int = 0,
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
            run_id=run_id,
            include_history=include_history,
            full=full,
            limit=limit,
            offset=offset,
            format=format,
            paginated=True,
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
    def run_workflow(
        workflow_key: str,
        payload: WorkflowRunRequest | None = None,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        request = payload or WorkflowRunRequest()
        execution_mode = request.execution_mode
        if request.task_key is not None:
            prepared = service.workflows.execute_task(
                actor=current_actor,
                workflow_key=workflow_key,
                task_key=request.task_key,
                task_version=request.task_version,
                execution_mode=execution_mode,
            )
            execution_mode = prepared["execution_mode"]
            task_version = prepared["task_version"]
        else:
            task_version = request.task_version
        started = service.workflow_scheduler.run_workflow_now(
            workflow_key,
            input_data=request.input,
            actor=current_actor,
            task_key=request.task_key,
            task_version=task_version,
            execution_mode=execution_mode,
        )
        return service.workflows.workflow_run_start_payload(started)

    @router.post("/workflows/{workflow_key}/run/preview")
    def preview_workflow_run(
        workflow_key: str,
        payload: WorkflowRunPreviewRequest | None = None,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        request = payload or WorkflowRunPreviewRequest()
        return service.workflows.preview_incremental_run(
            actor=current_actor,
            workflow_key=workflow_key,
            task_key=request.task_key,
            task_version=request.task_version,
            execution_mode=request.execution_mode,
        )

    @router.get("/workflows/{workflow_key}/tasks/import/template")
    def download_task_import_template(
        workflow_key: str,
        current_actor: str = Depends(actor),
    ) -> Response:
        content = service.workflows.build_task_import_template(
            actor=current_actor,
            workflow_key=workflow_key,
        )
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": 'attachment; filename="workflow-task-template.xlsx"'},
        )

    @router.post("/workflows/{workflow_key}/tasks/import/preview")
    async def preview_task_import(
        workflow_key: str,
        file: UploadFile = File(...),
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        return service.workflows.preview_task_import(
            actor=current_actor,
            workflow_key=workflow_key,
            filename=file.filename or "",
            content=await file.read(),
        )

    @router.post("/workflows/{workflow_key}/tasks/import/confirm")
    def confirm_task_import(
        workflow_key: str,
        payload: WorkflowTaskImportConfirmRequest,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        return service.workflows.confirm_task_import(
            actor=current_actor,
            workflow_key=workflow_key,
            import_id=payload.import_id,
        )

    @router.post("/workflows/{workflow_key}/tasks/{task_key:path}/execute")
    def execute_task(
        workflow_key: str,
        task_key: str,
        task_version: str | None = None,
        payload: WorkflowRunRequest | None = None,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        # Stamp the one-shot priority flag, then start a workflow run. The
        # flagged task is leased first by the agent's workflow_get_task call.
        request = payload or WorkflowRunRequest()
        if request.task_key is not None and request.task_key != task_key:
            raise HTTPException(status_code=400, detail="request task_key must match the path task_key")
        if task_version is not None and request.task_version is not None and task_version != request.task_version:
            raise HTTPException(status_code=400, detail="request task_version must match the query task_version")
        resolved_task_version = request.task_version if request.task_version is not None else task_version
        flagged = service.workflows.execute_task(
            actor=current_actor,
            workflow_key=workflow_key,
            task_key=task_key,
            task_version=resolved_task_version,
            execution_mode=request.execution_mode,
        )
        started = service.workflow_scheduler.run_workflow_now(
            workflow_key,
            input_data=request.input,
            actor=current_actor,
            task_key=task_key,
            task_version=flagged["task_version"],
            execution_mode=flagged["execution_mode"],
        )
        return {**flagged, **service.workflows.workflow_run_start_payload(started)}

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
