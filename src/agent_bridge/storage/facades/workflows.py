"""工作流的兼容方法。"""

from __future__ import annotations

from datetime import datetime
from typing import Any


class WorkflowsFacadeMixin:
    def upsert_workflow_definition(
        self,
        *,
        workflow_key: str,
        name: str,
        description: str,
        profile_key: str,
        status: str,
        created_by: str,
        workflow_type: str = "operation",
        definition: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.workflows.upsert_workflow_definition(
            workflow_key=workflow_key,
            name=name,
            description=description,
            profile_key=profile_key,
            definition=definition,
            status=status,
            created_by=created_by,
            workflow_type=workflow_type,
        )

    def get_workflow_definition(self, workflow_key: str) -> dict[str, Any] | None:
        return self.workflows.get_workflow_definition(workflow_key)

    def list_workflow_definitions(self) -> list[dict[str, Any]]:
        return self.workflows.list_workflow_definitions()

    def delete_workflow_definition(self, workflow_key: str) -> bool:
        return self.workflows.delete_workflow_definition(workflow_key)

    def upsert_workflow_tasks(self, workflow_key: str, tasks: list[dict[str, Any]]) -> dict[str, int]:
        return self.workflows.upsert_workflow_tasks(workflow_key, tasks)

    def preview_workflow_task_actions(
        self,
        workflow_key: str,
        tasks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self.workflows.preview_workflow_task_actions(workflow_key, tasks)

    def create_workflow_task_import(
        self,
        *,
        import_id: str,
        workflow_key: str,
        actor: str,
        filename: str,
        sheet_name: str,
        tasks: list[dict[str, Any]],
        preview: dict[str, Any],
        expires_at: datetime | str,
    ) -> dict[str, Any]:
        return self.workflows.create_workflow_task_import(
            import_id=import_id,
            workflow_key=workflow_key,
            actor=actor,
            filename=filename,
            sheet_name=sheet_name,
            tasks=tasks,
            preview=preview,
            expires_at=expires_at,
        )

    def get_workflow_task_import(self, import_id: str) -> dict[str, Any] | None:
        return self.workflows.get_workflow_task_import(import_id)

    def confirm_workflow_task_import(
        self,
        workflow_key: str,
        *,
        import_id: str,
        actor: str,
    ) -> dict[str, Any]:
        return self.workflows.confirm_workflow_task_import(
            workflow_key,
            import_id=import_id,
            actor=actor,
        )

    def delete_expired_workflow_task_imports(
        self,
        *,
        now: datetime | str | None = None,
    ) -> int:
        return self.workflows.delete_expired_workflow_task_imports(now=now)

    def get_workflow_task(
        self,
        workflow_key: str,
        task_key: str,
        task_version: str | None = None,
    ) -> dict[str, Any] | None:
        return self.workflows.get_workflow_task(workflow_key, task_key, task_version=task_version)

    def list_workflow_tasks(
        self,
        workflow_key: str,
        *,
        status: str | None = None,
        type: str | None = None,
        search: str | None = None,
        sort: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.workflows.list_workflow_tasks(
            workflow_key,
            status=status,
            type=type,
            search=search,
            sort=sort,
        )

    def lease_workflow_task(
        self,
        workflow_key: str,
        *,
        run_id: str,
        lease_seconds: int = 7200,
    ) -> dict[str, Any] | None:
        return self.workflows.lease_workflow_task(workflow_key, run_id=run_id, lease_seconds=lease_seconds)

    def lease_workflow_task_by_key(
        self,
        workflow_key: str,
        task_key: str,
        *,
        task_version: str,
        run_id: str,
        lease_seconds: int = 7200,
    ) -> dict[str, Any] | None:
        return self.workflows.lease_workflow_task_by_key(
            workflow_key,
            task_key,
            task_version=task_version,
            run_id=run_id,
            lease_seconds=lease_seconds,
        )

    def set_priority_for_task(
        self,
        workflow_key: str,
        task_key: str,
        *,
        task_version: str | None = None,
        flagged_at: str | None = None,
    ) -> bool:
        return self.workflows.set_priority_for_task(
            workflow_key, task_key, task_version=task_version, flagged_at=flagged_at
        )

    def reset_workflow_task(
        self,
        workflow_key: str,
        task_key: str,
        *,
        task_version: str | None = None,
    ) -> bool:
        return self.workflows.reset_workflow_task(workflow_key, task_key, task_version=task_version)

    def complete_workflow_task(
        self,
        workflow_key: str,
        task_key: str,
        *,
        task_version: str = "",
        run_id: str,
    ) -> bool:
        return self.workflows.complete_workflow_task(
            workflow_key,
            task_key,
            task_version=task_version,
            run_id=run_id,
        )

    def release_or_abandon_tasks_for_run(
        self,
        workflow_key: str,
        run_id: str,
        *,
        max_attempts: int,
        error_message: str,
    ) -> dict[str, int]:
        return self.workflows.release_or_abandon_tasks_for_run(
            workflow_key,
            run_id,
            max_attempts=max_attempts,
            error_message=error_message,
        )

    def force_workflow_task_lease_expiry(
        self,
        workflow_key: str,
        task_key: str,
        expires_at: str,
        task_version: str | None = None,
    ) -> None:
        return self.workflows.force_workflow_task_lease_expiry(
            workflow_key,
            task_key,
            expires_at,
            task_version=task_version,
        )

    def create_workflow_run(
        self,
        *,
        run_id: str,
        workflow_key: str,
        profile_key: str,
        task_key: str | None,
        status: str,
        temp_dir: str,
        definition_snapshot: dict[str, Any] | None = None,
        input_data: dict[str, Any] | None = None,
        workflow_revision_no: int | None = None,
        workflow_content_hash: str | None = None,
        task_version: str = "",
        execution_mode: str = "normal",
        execution_plan: dict[str, Any] | list[Any] | None = None,
        source_run_id: str | None = None,
    ) -> dict[str, Any]:
        return self.workflows.create_workflow_run(
            run_id=run_id,
            workflow_key=workflow_key,
            profile_key=profile_key,
            task_key=task_key,
            status=status,
            temp_dir=temp_dir,
            definition_snapshot=definition_snapshot,
            input_data=input_data,
            workflow_revision_no=workflow_revision_no,
            workflow_content_hash=workflow_content_hash,
            task_version=task_version,
            execution_mode=execution_mode,
            execution_plan=execution_plan,
            source_run_id=source_run_id,
        )

    def get_workflow_run(self, run_id: str) -> dict[str, Any] | None:
        return self.workflows.get_workflow_run(run_id)

    def list_workflow_definition_summaries(self) -> list[dict[str, Any]]:
        return self.workflows.list_workflow_definition_summaries()

    def list_workflow_runs(self, workflow_key: str, *, limit: int = 20) -> list[dict[str, Any]]:
        return self.workflows.list_workflow_runs(workflow_key, limit=limit)

    def list_workflow_run_summaries(
        self,
        workflow_key: str,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        return self.workflows.list_workflow_run_summaries(
            workflow_key,
            limit=limit,
            offset=offset,
        )

    def list_workflow_run_overviews(self) -> list[dict[str, Any]]:
        return self.workflows.list_workflow_run_overviews()

    def clear_workflow_execution_data(self, workflow_key: str) -> dict[str, int]:
        return self.workflows.clear_workflow_execution_data(workflow_key)

    def finish_workflow_run(
        self,
        run_id: str,
        *,
        expected_status: str = "running",
        status: str,
        exit_code: int | None,
        error: str | None,
        duration_ms: int | None,
        output: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.workflows.finish_workflow_run(
            run_id,
            expected_status=expected_status,
            status=status,
            exit_code=exit_code,
            error=error,
            duration_ms=duration_ms,
            output=output,
        )

    def create_workflow_node_runs(self, run_id: str, nodes: list[dict[str, Any]]) -> None:
        self.workflows.create_workflow_node_runs(run_id, nodes)

    def start_workflow_node_run(
        self, run_id: str, node_id: str, condition_results: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        return self.workflows.start_workflow_node_run(run_id, node_id, condition_results)

    def finish_workflow_node_run(self, run_id: str, node_id: str, **kwargs: Any) -> dict[str, Any]:
        return self.workflows.finish_workflow_node_run(run_id, node_id, **kwargs)

    def list_workflow_node_runs(self, run_id: str) -> list[dict[str, Any]]:
        return self.workflows.list_workflow_node_runs(run_id)

    def associate_workflow_run_artifacts(
        self,
        run_id: str,
        node_id: str,
        artifact_ids: list[str],
        *,
        source_run_id: str | None = None,
        source_node_id: str | None = None,
    ) -> None:
        self.workflows.associate_workflow_run_artifacts(
            run_id,
            node_id,
            artifact_ids,
            source_run_id=source_run_id,
            source_node_id=source_node_id,
        )

    def list_workflow_run_artifacts(
        self,
        run_id: str,
        *,
        node_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.workflows.list_workflow_run_artifacts(run_id, node_id=node_id)

    def fail_workflow_task_for_run(self, workflow_key: str, run_id: str, error_message: str) -> bool:
        return self.workflows.fail_workflow_task_for_run(workflow_key, run_id, error_message)

    def append_workflow_run_log(
        self,
        *,
        run_id: str,
        workflow_key: str,
        task_key: str | None,
        level: str,
        stage: str,
        message: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self.workflows.append_workflow_run_log(
            run_id=run_id,
            workflow_key=workflow_key,
            task_key=task_key,
            level=level,
            stage=stage,
            message=message,
            payload=payload,
        )

    def list_workflow_run_logs(self, run_id: str) -> list[dict[str, Any]]:
        return self.workflows.list_workflow_run_logs(run_id)

    def upsert_workflow_artifact(
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
        task_version: str = "",
        producer_node_id: str | None = None,
        producer_node_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        return self.workflows.upsert_workflow_artifact(
            workflow_key=workflow_key,
            profile_key=profile_key,
            run_id=run_id,
            task_key=task_key,
            task_version=task_version,
            title=title,
            path=path,
            tags=tags,
            format=format,
            summary=summary,
            content=content,
            metadata=metadata,
            producer_node_id=producer_node_id,
            producer_node_fingerprint=producer_node_fingerprint,
        )

    def get_workflow_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        return self.workflows.get_workflow_artifact(artifact_id)

    def list_artifacts_for_run(self, run_id: str, *, include_reused: bool = True) -> list[dict[str, Any]]:
        return self.workflows.list_artifacts_for_run(run_id, include_reused=include_reused)

    def search_workflow_artifacts(
        self,
        *,
        profile_key: str | None,
        query: str | None,
        tags: list[str],
        path: str | None,
        workflow_key: str | None,
        limit: int,
        task_key: str | None = None,
        task_version: str | None = None,
        run_id: str | None = None,
        include_history: bool = False,
        format: str | None = None,
        path_match: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.workflows.search_workflow_artifacts(
            profile_key=profile_key,
            query=query,
            tags=tags,
            path=path,
            workflow_key=workflow_key,
            task_key=task_key,
            task_version=task_version,
            run_id=run_id,
            include_history=include_history,
            limit=limit,
            format=format,
            path_match=path_match,
        )
