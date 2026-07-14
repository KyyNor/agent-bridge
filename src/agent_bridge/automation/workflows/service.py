from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from agent_bridge.core.domain import AccessDenied, NotFound, ValidationError, require_admin_user
from agent_bridge.storage.sqlite import SQLiteStore
from agent_bridge.automation.workflows.models import (
    WorkflowArtifactFormat,
    WorkflowStatus,
    WorkflowType,
)
from agent_bridge.automation.workflows.result_parser import ParsedWorkflowResult

logger = logging.getLogger(__name__)


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
    def __init__(
        self,
        *,
        store: SQLiteStore,
        admins: set[str],
        agent_service: Any = None,
        skills: Any = None,
    ) -> None:
        self.store = store
        self.admins = admins
        # Late-wired collaborators (set by AgentBridgeService after both the
        # agent service and skill service are constructed). Kept optional so
        # this service stays usable in isolated tests.
        self.agent_service = agent_service
        self.skills = skills

    def upsert_definition(
        self,
        *,
        actor: str,
        workflow_key: str,
        name: str,
        description: str,
        profile_key: str,
        workflow_js: str,
        status: str,
        workflow_type: str = "operation",
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        if self.store.get_project_profile(profile_key) is None:
            raise ValidationError("profile not found")
        try:
            next_status = WorkflowStatus(status).value
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        try:
            next_type = WorkflowType(workflow_type).value
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        result = self.store.upsert_workflow_definition(
            workflow_key=workflow_key,
            name=name,
            description=description,
            profile_key=profile_key,
            workflow_js=workflow_js,
            status=next_status,
            workflow_type=next_type,
            created_by=actor,
        )
        logger.info(
            "Workflow 定义已保存 workflow=%s profile=%s 状态=%s 类型=%s actor=%s",
            workflow_key,
            profile_key,
            next_status,
            next_type,
            actor,
        )
        return result

    def list_definitions(self, actor: str) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        return self.store.list_workflow_definitions()

    def get_definition(self, actor: str, workflow_key: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        workflow = self.store.get_workflow_definition(workflow_key)
        if workflow is None:
            raise NotFound("workflow not found")
        return workflow

    def delete_definition(self, actor: str, workflow_key: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        if self.store.get_workflow_definition(workflow_key) is None:
            raise NotFound("workflow not found")
        self.store.delete_workflow_definition(workflow_key)
        logger.info("Workflow 定义已删除 workflow=%s actor=%s", workflow_key, actor)
        return {"workflow_key": workflow_key, "deleted": True}

    def clear_execution_data(self, actor: str, workflow_key: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        if self.store.get_workflow_definition(workflow_key) is None:
            raise NotFound("workflow not found")
        counts = self.store.clear_workflow_execution_data(workflow_key)
        return {"workflow_key": workflow_key, "cleared": True, **counts}

    def list_runs(self, actor: str, workflow_key: str, *, limit: int = 20) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        bounded = min(max(limit, 1), 200)
        return self.store.list_workflow_runs(workflow_key, limit=bounded)

    def list_tasks(
        self,
        actor: str,
        workflow_key: str,
        *,
        status: str | None = None,
        type: str | None = None,
        search: str | None = None,
        sort: str | None = None,
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        if self.store.get_workflow_definition(workflow_key) is None:
            raise NotFound("workflow not found")
        return {
            "tasks": self.store.list_workflow_tasks(
                workflow_key, status=status, type=type, search=search, sort=sort
            )
        }

    @staticmethod
    def _is_leasable(task: dict[str, Any]) -> bool:
        """Mirror of lease_workflow_task's eligibility: pending, or running
        with an expired lease."""
        status = task.get("status")
        if status == "pending":
            return True
        if status == "running":
            expires_at = task.get("lease_expires_at")
            if expires_at:
                try:
                    return datetime.fromisoformat(expires_at) < datetime.now(timezone.utc)
                except ValueError:
                    return False
        return False

    def execute_task(
        self,
        *,
        actor: str,
        workflow_key: str,
        task_key: str,
        task_version: str | None = None,
    ) -> dict[str, Any]:
        """Mark a single task for priority execution.

        Stamps a one-shot priority flag so the next ``workflow_get_task`` lease
        picks this task ahead of normal id ordering, then (in the route layer)
        a workflow run is started. Does not itself start the run. The task must
        be currently leasable (pending or lease-expired); otherwise the caller
        must reset it first.
        """
        require_admin_user(actor, self.admins)
        if self.store.get_workflow_definition(workflow_key) is None:
            raise NotFound("workflow not found")
        task = self.store.get_workflow_task(workflow_key, task_key, task_version=(task_version or None))
        if task is None:
            raise NotFound("workflow task not found")
        if not self._is_leasable(task):
            raise ValidationError("task is not currently executable; reset it first")
        resolved_task_version = str(task.get("task_version") or "")
        self.store.set_priority_for_task(workflow_key, task_key, task_version=resolved_task_version)
        logger.info(
            "Workflow 任务标记优先执行 workflow=%s task=%s version=%s actor=%s",
            workflow_key,
            task_key,
            resolved_task_version,
            actor,
        )
        return {
            "workflow_key": workflow_key,
            "task_key": task_key,
            "task_version": resolved_task_version,
            "priority": True,
        }

    def reset_task(
        self,
        *,
        actor: str,
        workflow_key: str,
        task_key: str,
        task_version: str | None = None,
    ) -> dict[str, Any]:
        """Reset a task to a leasable state without triggering execution.

        Clears status/lease/completion/priority so the task can be picked up by
        the next ``workflow_get_task`` call. Does not start a run and does not
        change queue ordering. ``attempt_count`` and ``last_error`` are kept as
        an audit trail.
        """
        require_admin_user(actor, self.admins)
        if self.store.get_workflow_definition(workflow_key) is None:
            raise NotFound("workflow not found")
        task = self.store.get_workflow_task(workflow_key, task_key, task_version=(task_version or None))
        if task is None:
            raise NotFound("workflow task not found")
        if task.get("status") == "running" and not self._is_leasable(task):
            raise ValidationError("task is currently running; wait for the lease to expire or stop the run first")
        resolved_task_version = str(task.get("task_version") or "")
        updated = self.store.reset_workflow_task(workflow_key, task_key, task_version=resolved_task_version)
        if not updated:
            raise NotFound("workflow task not found")
        task = self.store.get_workflow_task(workflow_key, task_key, task_version=resolved_task_version)
        logger.info(
            "Workflow 任务已重置 workflow=%s task=%s version=%s actor=%s",
            workflow_key,
            task_key,
            resolved_task_version,
            actor,
        )
        return {
            "workflow_key": workflow_key,
            "task_key": task_key,
            "task_version": resolved_task_version,
            "status": task["status"],
        }

    def get_run(self, actor: str, run_id: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        run = self.store.get_workflow_run(run_id)
        if run is None:
            raise NotFound("workflow run not found")
        return run

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

    def require_workflow_context(
        self,
        *,
        profile_key: str | None,
        workflow_key: str | None,
    ) -> dict[str, Any]:
        if not workflow_key:
            raise ValidationError("workflow context is required")
        workflow = self.store.get_workflow_definition(workflow_key)
        if workflow is None:
            raise NotFound("workflow not found")
        if profile_key and workflow["profile_key"] != profile_key:
            raise ValidationError("workflow profile mismatch")
        return workflow

    def require_workflow_run_context(
        self,
        *,
        profile_key: str | None,
        workflow_key: str | None,
        run_id: str | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        workflow = self.require_workflow_context(profile_key=profile_key, workflow_key=workflow_key)
        if not run_id:
            raise ValidationError("workflow run id is required")
        run = self.store.get_workflow_run(run_id)
        if run is None:
            raise NotFound("workflow run not found")
        if run["workflow_key"] != workflow["workflow_key"] or run["profile_key"] != workflow["profile_key"]:
            raise ValidationError("workflow run context mismatch")
        return workflow, run

    def get_task_for_agent(self, *, profile_key: str | None, workflow_key: str, run_id: str) -> dict[str, Any]:
        self.require_workflow_run_context(profile_key=profile_key, workflow_key=workflow_key, run_id=run_id)
        task = self.store.lease_workflow_task(workflow_key, run_id=run_id, lease_seconds=7200)
        return {"task": task}

    def set_tasks_for_agent(
        self,
        *,
        profile_key: str | None,
        workflow_key: str,
        run_id: str,
        tasks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self.require_workflow_run_context(profile_key=profile_key, workflow_key=workflow_key, run_id=run_id)
        normalized = []
        for task in tasks:
            task_key = str(task.get("task_key") or "").strip()
            if not task_key:
                raise ValidationError("task_key is required")
            task_version = str(task.get("task_version") or "")
            task_type = str(task.get("type") or "")
            payload = task.get("payload")
            if payload is None:
                payload = {}
            if not isinstance(payload, dict):
                raise ValidationError("task payload must be an object")
            normalized.append({"task_key": task_key, "task_version": task_version, "type": task_type, "payload": payload})
        return self.store.upsert_workflow_tasks(workflow_key, normalized)

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
        task_version: str = "",
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
        saved = self.store.upsert_workflow_artifact(
            workflow_key=workflow_key,
            profile_key=profile_key,
            run_id=run_id,
            task_key=task_key,
            task_version=task_version,
            title=title,
            path=path,
            tags=tags,
            format=artifact_format,
            summary=summary,
            content=content,
            metadata=metadata,
        )
        logger.info(
            "workflow 产物已保存 run_id=%s path=%s workflow=%s task=%s",
            run_id,
            path,
            workflow_key,
            task_key,
        )
        return saved

    def ingest_parsed_result(
        self,
        *,
        workflow_key: str,
        profile_key: str,
        run_id: str,
        parsed: ParsedWorkflowResult,
    ) -> dict[str, Any]:
        self.require_workflow_run_context(profile_key=profile_key, workflow_key=workflow_key, run_id=run_id)
        if parsed.status == "no_executable_task":
            return {"status": "no_task", "artifact_count": 0}
        if parsed.status != "completed" or not parsed.task_key:
            raise ValidationError("parsed workflow result is not ingestible")
        saved = [
            self.save_artifact(
                workflow_key=workflow_key,
                profile_key=profile_key,
                run_id=run_id,
                task_key=parsed.task_key,
                task_version=parsed.task_version,
                title=artifact.title,
                path=artifact.path,
                tags=artifact.tags,
                format=artifact.format,
                summary=artifact.summary,
                content=artifact.content,
                metadata=artifact.metadata,
            )
            for artifact in parsed.artifacts
        ]
        completed = self.store.complete_workflow_task(
            workflow_key,
            parsed.task_key,
            task_version=parsed.task_version,
            run_id=run_id,
        )
        if not completed:
            raise ValidationError("workflow task lease mismatch")
        return {"status": "completed", "artifact_count": len(saved), "artifacts": saved}

    def generate_html_report_for_run(
        self,
        *,
        workflow_key: str,
        profile_key: str,
        run_id: str,
        actor: str,
    ) -> dict[str, Any]:
        """Generate a human-readable HTML report for a completed summary run.

        Only fires for ``workflow_type='summary'`` workflows that produced at
        least one Markdown artifact this run. The report is one consolidated
        HTML document stored at ``out/report.html`` (overwriting any previous
        report for the same task via the existing ``is_current`` mechanism).

        Failures here must NOT alter the main workflow run status — callers
        (the scheduler) wrap this in try/except and log a warning.
        """
        # Local import to avoid a module-load cycle (reporter -> models).
        from agent_bridge.automation.workflows.reporter import (
            HTML_MAX_BYTES,
            HTML_REPORT_SCHEMA,
            build_report_prompt,
            looks_like_html,
            summarize_agent_events,
        )
        from agent_bridge.automation.workflows.models import WorkflowType

        workflow = self.store.get_workflow_definition(workflow_key)
        if workflow is None:
            return {"status": "skipped", "reason": "workflow not found"}
        if (workflow.get("workflow_type") or WorkflowType.operation.value) != WorkflowType.summary.value:
            return {"status": "skipped", "reason": "not a summary workflow"}

        markdown_items = self.store.search_workflow_artifacts(
            profile_key=profile_key,
            query=None,
            tags=[],
            path=None,
            workflow_key=workflow_key,
            run_id=run_id,
            include_history=False,
            format="markdown",
            limit=50,
        )
        if not markdown_items:
            return {"status": "no_markdown"}

        # Derive the task_key from the markdown artifacts so the HTML report
        # shares it (and thus participates in the same is_current overwrite).
        task_key = markdown_items[0].get("task_key")
        task_version = markdown_items[0].get("task_version") or ""

        run_logs = self.store.list_workflow_run_logs(run_id)
        agent_runs = self.store.agent_runs.list(
            workflow_key=workflow_key, workflow_run_id=run_id, limit=50
        )
        agent_events_summary = summarize_agent_events(agent_runs)

        if self.agent_service is None:
            raise ValidationError("agent service is not configured")
        if self.skills is None:
            raise ValidationError("skill service is not configured")

        skill_payload = self.skills.get_skill(actor, "design_html_report")
        skill_prompt = skill_payload["prompt"]
        run_row = self.store.get_workflow_run(run_id) or {}

        prompt = build_report_prompt(
            skill_name="design_html_report",
            skill_prompt=skill_prompt,
            workflow=workflow,
            run={"run_id": run_id, "task_key": task_key},
            markdown_artifacts=markdown_items,
            run_logs=run_logs,
            agent_events_summary=agent_events_summary,
        )

        import asyncio

        result = asyncio.run(
            self.agent_service.run(
                prompt=prompt,
                agent_name="workflow_html_reporter",
                profile=profile_key,
                output_schema=HTML_REPORT_SCHEMA,
                actor=actor,
                workflow_key=workflow_key,
                run_id=run_id,
                timeout=900,
            )
        )

        if not result.ok:
            raise ValidationError(f"html report agent failed: {result.error or 'unknown'}")
        payload = result.result or {}
        if not isinstance(payload, dict):
            raise ValidationError("html report agent returned non-object result")

        html = str(payload.get("html") or "")
        if not looks_like_html(html):
            raise ValidationError("html report agent output is not a valid HTML document")
        if len(html.encode("utf-8")) > HTML_MAX_BYTES:
            raise ValidationError("html report exceeds size limit")

        title = str(payload.get("title") or "Workflow 报告")[:200]
        summary = str(payload.get("summary") or "")[:2000]
        source_ids = [str(x) for x in payload.get("source_artifact_ids") or [] if isinstance(x, str)]

        saved = self.save_artifact(
            workflow_key=workflow_key,
            profile_key=profile_key,
            run_id=run_id,
            task_key=task_key,
            task_version=task_version,
            title=title,
            path="out/report.html",
            tags=["html-report"],
            format="html",
            summary=summary,
            content=html,
            metadata={
                "derived_from_artifact_ids": source_ids,
                "report_kind": "human_html",
            },
        )
        logger.info(
            "Workflow HTML 报告已生成 run_id=%s workflow=%s bytes=%d",
            run_id,
            workflow_key,
            len(html.encode("utf-8")),
        )
        return {"status": "generated", "artifact": saved}

    def get_artifact(
        self,
        *,
        actor: str,
        artifact_id: str,
        profile_key: str | None = None,
        trusted_profile_context: bool = False,
    ) -> dict[str, Any]:
        item = self.store.get_workflow_artifact(artifact_id)
        if item is None:
            raise NotFound("workflow artifact not found")
        if actor not in self.admins:
            if not profile_key:
                raise AccessDenied("capability profile is required")
            if not trusted_profile_context:
                raise AccessDenied("profile context is not trusted")
            if item["profile_key"] != profile_key:
                raise NotFound("workflow artifact not found")
        return {
            "artifact_id": item["artifact_id"],
            "workflow_key": item["workflow_key"],
            "profile_key": item["profile_key"],
            "run_id": item["run_id"],
            "task_key": item["task_key"],
            "task_version": item["task_version"],
            "is_current": item["is_current"],
            "title": item["title"],
            "path": item["path"],
            "tags": item["tags"],
            "format": item["format"],
            "summary": item["summary"],
            "content": item["content"],
            "metadata": item["metadata"],
            "created_at": item["created_at"],
            "updated_at": item["updated_at"],
        }

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
        offset: int = 0,
        task_key: str | None = None,
        task_version: str | None = None,
        run_id: str | None = None,
        include_history: bool = False,
        trusted_profile_context: bool = False,
        full: bool = False,
        format: str | None = None,
        paginated: bool = False,
    ) -> dict[str, Any]:
        if actor not in self.admins and not profile_key:
            raise AccessDenied("capability profile is required")
        if actor not in self.admins and profile_key and not trusted_profile_context:
            raise AccessDenied("profile context is not trusted")
        if profile_key:
            profile = self.store.get_project_profile(profile_key)
            if profile is None:
                raise NotFound("profile not found")
            if profile.get("status") != "active":
                raise ValidationError("profile is disabled")
        if limit < 1:
            raise ValidationError("limit must be positive")
        bounded_limit = min(limit, 50)
        bounded_offset = max(offset, 0)
        if paginated:
            page = self.store.workflows.search_workflow_artifacts_page(
                profile_key=profile_key,
                query=query,
                tags=tags,
                path=path,
                workflow_key=workflow_key,
                task_key=task_key,
                task_version=task_version,
                run_id=run_id,
                include_history=include_history,
                limit=bounded_limit,
                offset=bounded_offset,
                format=format,
            )
            items = page["items"]
        else:
            items = self.store.search_workflow_artifacts(
                profile_key=profile_key,
                query=query,
                tags=tags,
                path=path,
                workflow_key=workflow_key,
                task_key=task_key,
                task_version=task_version,
                run_id=run_id,
                include_history=include_history,
                limit=bounded_limit,
                format=format,
            )
        def _entry(item: dict[str, Any]) -> dict[str, Any]:
            entry = {
                "artifact_id": item["artifact_id"],
                "workflow_key": item["workflow_key"],
                "profile_key": item["profile_key"],
                "run_id": item["run_id"],
                "task_key": item["task_key"],
                "task_version": item["task_version"],
                "is_current": item["is_current"],
                "title": item["title"],
                "path": item["path"],
                "tags": item["tags"],
                "format": item["format"],
                "summary": item["summary"],
                "snippet": _snippet(item["content"], query),
                "created_at": item["created_at"],
                "updated_at": item["updated_at"],
            }
            # Return the full body when explicitly requested (feature: view a
            # task's outputs from the progress page) or on an exact-path lookup
            # ("fetch this one"). Prefix matches keep snippet-only otherwise.
            if full or (path and item["path"] == path):
                entry["content"] = item["content"]
            return entry

        result = {"items": [_entry(item) for item in items]}
        if paginated:
            result.update(
                {
                    "total": page["total"],
                    "limit": page["limit"],
                    "offset": page["offset"],
                }
            )
        return result

    def list_artifact_history(
        self,
        *,
        actor: str,
        profile_key: str | None,
        workflow_key: str,
        task_key: str,
        limit: int,
        trusted_profile_context: bool = False,
    ) -> dict[str, Any]:
        if actor not in self.admins and not profile_key:
            raise AccessDenied("capability profile is required")
        if actor not in self.admins and profile_key and not trusted_profile_context:
            raise AccessDenied("profile context is not trusted")
        if not workflow_key:
            raise ValidationError("workflow_key is required")
        if not task_key:
            raise ValidationError("task_key is required")
        if limit < 1:
            raise ValidationError("limit must be positive")
        bounded_limit = min(limit, 50)
        items = self.store.search_workflow_artifacts(
            profile_key=profile_key,
            query=None,
            tags=[],
            path=None,
            workflow_key=workflow_key,
            task_key=task_key,
            task_version=None,
            include_history=True,
            limit=200,
        )

        versions: list[dict[str, Any]] = []
        by_version: dict[str, dict[str, Any]] = {}
        for item in items:
            version = item["task_version"]
            entry = by_version.get(version)
            if entry is None:
                entry = {
                    "workflow_key": item["workflow_key"],
                    "task_key": item["task_key"],
                    "task_version": version,
                    "is_current": item["is_current"],
                    "run_id": item["run_id"],
                    "updated_at": item["updated_at"],
                    "artifacts": [],
                }
                by_version[version] = entry
                versions.append(entry)
            entry["is_current"] = bool(entry["is_current"] or item["is_current"])
            if item["updated_at"] > entry["updated_at"]:
                entry["updated_at"] = item["updated_at"]
                entry["run_id"] = item["run_id"]
            entry["artifacts"].append(
                {
                    "artifact_id": item["artifact_id"],
                    "run_id": item["run_id"],
                    "title": item["title"],
                    "path": item["path"],
                    "tags": item["tags"],
                    "format": item["format"],
                    "summary": item["summary"],
                    "content": item["content"],
                    "created_at": item["created_at"],
                    "updated_at": item["updated_at"],
                }
            )

        return {"versions": versions[:bounded_limit]}
