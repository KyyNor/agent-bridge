from __future__ import annotations

from typing import Any

from agent_bridge.core.domain import NotFound, ValidationError, require_admin_user
from agent_bridge.storage.sqlite import SQLiteStore
from agent_bridge.workflows.models import WorkflowArtifactFormat, WorkflowStatus, require_manifest


def _snippet(content: str, query: str | None, size: int = 220) -> str:
    if not query:
        return content[:size]
    index = content.lower().find(query.lower())
    if index < 0:
        return content[:size]
    start = max(0, index - 60)
    end = min(len(content), index + size - 60)
    return content[start:end]


class WorkflowService:
    def __init__(self, *, store: SQLiteStore, admins: set[str]) -> None:
        self.store = store
        self.admins = admins

    def upsert_definition(
        self,
        *,
        actor: str,
        workflow_key: str,
        name: str,
        description: str,
        profile_key: str,
        workflow_js: str,
        manifest: dict[str, Any],
        schedule: dict[str, Any],
        status: str,
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        if self.store.get_project_profile(profile_key) is None:
            raise ValidationError("profile not found")
        try:
            require_manifest(manifest)
            next_status = WorkflowStatus(status).value
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        return self.store.upsert_workflow_definition(
            workflow_key=workflow_key,
            name=name,
            description=description,
            profile_key=profile_key,
            workflow_js=workflow_js,
            manifest=manifest,
            schedule=schedule,
            status=next_status,
            created_by=actor,
        )

    def list_definitions(self, actor: str) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        return self.store.list_workflow_definitions()

    def get_definition(self, actor: str, workflow_key: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        workflow = self.store.get_workflow_definition(workflow_key)
        if workflow is None:
            raise NotFound("workflow not found")
        return workflow

    def append_run_log(
        self,
        *,
        workflow_key: str,
        run_id: str,
        task_key: str | None,
        level: str,
        stage: str,
        message: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self.store.append_workflow_run_log(
            run_id=run_id,
            workflow_key=workflow_key,
            task_key=task_key,
            level=level,
            stage=stage,
            message=message,
            payload=payload,
        )

    def list_run_logs(self, actor: str, run_id: str) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        return self.store.list_workflow_run_logs(run_id)

    def save_artifact(
        self,
        *,
        workflow_key: str,
        profile_key: str,
        run_id: str,
        task_key: str | None,
        title: str,
        path: str,
        tags: list[str],
        format: str,
        summary: str,
        content: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        workflow = self.store.get_workflow_definition(workflow_key)
        if workflow is None:
            raise NotFound("workflow not found")
        if workflow["profile_key"] != profile_key:
            raise ValidationError("workflow profile mismatch")
        try:
            artifact_format = WorkflowArtifactFormat(format).value
        except ValueError as exc:
            raise ValidationError("unsupported artifact format") from exc
        if not path or path.startswith("/") or ".." in path.split("/"):
            raise ValidationError("invalid artifact path")
        if artifact_format != WorkflowArtifactFormat.markdown.value:
            raise ValidationError("unsupported artifact format")
        return self.store.upsert_workflow_artifact(
            workflow_key=workflow_key,
            profile_key=profile_key,
            run_id=run_id,
            task_key=task_key,
            title=title,
            path=path,
            tags=tags,
            format=artifact_format,
            summary=summary,
            content=content,
            metadata=metadata,
        )

    def search_artifacts(
        self,
        *,
        actor: str,
        profile_key: str | None,
        query: str | None,
        tags: list[str],
        path: str | None,
        workflow_key: str | None,
        limit: int,
    ) -> dict[str, Any]:
        if actor not in self.admins and not profile_key:
            raise ValidationError("profile_key is required")
        if profile_key:
            profile = self.store.get_project_profile(profile_key)
            if profile is None:
                raise NotFound("profile not found")
            if profile.get("status") != "active":
                raise ValidationError("profile is disabled")
        if limit < 1:
            raise ValidationError("limit must be positive")
        bounded_limit = min(limit, 50)
        items = self.store.search_workflow_artifacts(
            profile_key=profile_key,
            query=query,
            tags=tags,
            path=path,
            workflow_key=workflow_key,
            limit=bounded_limit,
        )
        return {
            "items": [
                {
                    "artifact_id": item["artifact_id"],
                    "workflow_key": item["workflow_key"],
                    "profile_key": item["profile_key"],
                    "run_id": item["run_id"],
                    "task_key": item["task_key"],
                    "title": item["title"],
                    "path": item["path"],
                    "tags": item["tags"],
                    "format": item["format"],
                    "summary": item["summary"],
                    "snippet": _snippet(item["content"], query),
                    "created_at": item["created_at"],
                    "updated_at": item["updated_at"],
                }
                for item in items
            ]
        }
