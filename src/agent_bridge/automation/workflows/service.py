from __future__ import annotations

from collections import Counter
import json
import logging
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from agent_bridge.access_control.resources import ScopedResourceType
from agent_bridge.access_control.service import AccessControlService, ResourceScope
from agent_bridge.core.domain import ConflictError, NotFound, ValidationError, require_admin_user
from agent_bridge.core.diff import text_diff, workflow_structured_diff
from agent_bridge.core.timeutil import local_now, parse_utc, utc_iso, utc_now
from agent_bridge.storage.sqlite import SQLiteStore
from agent_bridge.automation.workflows.models import (
    WorkflowStatus,
    WorkflowType,
)
from agent_bridge.automation.workflows.task_import import (
    TaskImportFormatError,
    build_task_import_template as build_task_import_template_file,
    parse_task_import,
)
from agent_bridge.automation.workflows.definition import WorkflowGraph
from agent_bridge.automation.workflows.incremental import (
    IncrementalPlan,
    build_incremental_plan as _build_incremental_plan,
    incremental_plan_from_payload,
    incremental_plan_payload,
    incremental_plan_preview_payload,
)
from agent_bridge.automation.workflows.artifact_service import ArtifactService
from agent_bridge.automation.workflows.definition_import import DefinitionImportService
from agent_bridge.automation.workflows.report_renderer import render_run_html_report
from agent_bridge.automation.workflows.revisions import (
    definition_payload,
    workflow_content_hash,
    workflow_revision_snapshot,
    workflow_version_hash,
)
from agent_bridge.automation.workflows.validation import (
    WorkflowDefinitionValidationError,
    WorkflowValidationIssue,
)
from agent_bridge.automation.workflows.validator import WorkflowValidator
from agent_bridge.automation.workflows.window import parse_hhmm, previous_window_bounds

logger = logging.getLogger(__name__)

WORKFLOW_REVISION_SOURCES = frozenset({"edit", "import", "restore"})
WORKFLOW_TASK_REFRESH_POLICIES = frozenset({"auto", "defer"})


class WorkflowService:
    def __init__(
        self,
        *,
        store: SQLiteStore,
        admins: set[str],
        agent_service: Any = None,
        skills: Any = None,
        scripts: Any = None,
        artifact_search_cache_dir: Path | None = None,
        access: AccessControlService | None = None,
    ) -> None:
        self.store = store
        self.admins = admins
        self.access = access
        # Late-wired collaborators (set by AgentBridgeService after both the
        # agent service and skill service are constructed). Kept optional so
        # this service stays usable in isolated tests.
        self.agent_service = agent_service
        self.skills = skills
        self.scripts = scripts
        self.validator = WorkflowValidator(
            store=store,
            agent_service=agent_service,
            skills=skills,
            scripts=scripts,
        )
        self._artifacts = ArtifactService(
            store=store,
            admins=admins,
            workflow_service=self,
            cache_dir=artifact_search_cache_dir,
            access=access,
        )
        self._imports = DefinitionImportService(
            store=store, admins=admins, validator=self.validator,
            upsert_definition=self.upsert_definition,
            require_read=lambda actor, key: self._require_workflow_read(actor, key),
            require_write=lambda actor, key: self._require_workflow_write(actor, key),
            require_create=self._require_workflow_create,
        )

    def upsert_definition(
        self,
        *,
        actor: str,
        workflow_key: str,
        name: str,
        description: str,
        profile_key: str,
        status: str,
        workflow_type: str = "operation",
        definition: dict[str, Any] | WorkflowGraph | None = None,
        revision_source: str = "edit",
        expected_edit_version: int | None = None,
        task_refresh_policy: str = "auto",
    ) -> dict[str, Any]:
        current_scope = self.store.get_workflow_definition(workflow_key)
        if current_scope is None:
            owner_group_key = self._require_workflow_create(actor, profile_key)
            scope = ResourceScope.from_record(
                {"owner_group_key": owner_group_key, "visibility": "group"}
            )
        else:
            self._require_workflow_write(actor, workflow_key)
            scope = ResourceScope.from_record(current_scope)
            self._require_profile_same_group(actor, profile_key, scope.owner_group_key)
        if revision_source not in WORKFLOW_REVISION_SOURCES:
            raise ValidationError(f"unsupported workflow revision source: {revision_source}")
        if task_refresh_policy not in WORKFLOW_TASK_REFRESH_POLICIES:
            raise ValidationError(f"unsupported workflow task refresh policy: {task_refresh_policy}")
        graph = self.validator.require_valid(
            actor=actor,
            workflow={
                "workflow_key": workflow_key,
                "name": name,
                "description": description,
                "profile_key": profile_key,
                "definition": definition or {"nodes": [], "edges": []},
                "status": status,
                "workflow_type": workflow_type,
            },
        )
        next_status = WorkflowStatus(status).value
        next_type = WorkflowType(workflow_type).value
        graph_payload = graph.model_dump(mode="json")
        # 执行语义口径（剥离 name/description/timeout/title）：喂给重跑/stale 判定。
        new_content_hash = workflow_content_hash(
            graph_payload, name, description, profile_key, next_status, next_type
        )
        # 版本口径（含 name/description、节点保留展示与运行控制字段）：喂给版本号递增判定。
        new_version_hash = workflow_version_hash(
            graph_payload, name, description, profile_key, next_status, next_type
        )
        with self.store.transaction():
            current = self.store.get_workflow_definition(workflow_key)
            if expected_edit_version is not None:
                current_edit_version = int(current.get("edit_version") or 1) if current else 0
                if current_edit_version != expected_edit_version:
                    logger.warning(
                        "Workflow 保存被拒绝：编辑版本冲突 workflow=%s actor=%s expected=%s current=%s",
                        workflow_key,
                        actor,
                        expected_edit_version,
                        current_edit_version,
                    )
                    raise ConflictError(
                        "工作流已在其他页面更新或目标标识已被占用，请刷新后重新编辑"
                    )
            previous_revisions = self.store.workflows.list_definition_revisions(workflow_key, limit=1)
            previous_version_hash = previous_revisions[0]["version_hash"] if previous_revisions else None
            result = self.store.upsert_workflow_definition(
                workflow_key=workflow_key,
                name=name,
                description=description,
                profile_key=profile_key,
                definition=graph_payload,
                status=next_status,
                workflow_type=next_type,
                created_by=actor,
                owner_group_key=scope.owner_group_key,
            )
            # 版本口径判定：name/description/timeout/title 等展示或运行控制字段变更
            # 也应产生新版本号（供版本历史与 diff），但不影响重跑。
            content_changed = not previous_revisions or previous_version_hash != new_version_hash
            revision_no = previous_revisions[0]["revision_no"] if previous_revisions else 0
            tasks_marked_stale = 0
            if content_changed:
                revision = self.store.workflows.create_definition_revision(
                    workflow_key=workflow_key,
                    content_hash=new_content_hash,
                    snapshot=workflow_revision_snapshot(result),
                    actor=actor,
                    source=revision_source,
                    version_hash=new_version_hash,
                    task_refresh_policy=task_refresh_policy,
                )
                revision_no = revision["revision_no"]
                if task_refresh_policy == "auto":
                    # 重跑/stale 判定只用执行语义口径 hash，与版本号解耦。
                    tasks_marked_stale = self.store.workflows.mark_latest_task_stale_if_needed(
                        workflow_key, new_content_hash
                    )
        result["content_hash"] = new_content_hash
        result["version_hash"] = new_version_hash
        result["revision_no"] = revision_no
        result["task_refresh_policy"] = task_refresh_policy
        result["tasks_marked_stale"] = tasks_marked_stale
        logger.info(
            "Workflow 定义已保存 workflow=%s profile=%s 状态=%s 类型=%s actor=%s",
            workflow_key,
            profile_key,
            next_status,
            next_type,
            actor,
        )
        return definition_payload(result)

    def list_definitions(self, actor: str) -> list[dict[str, Any]]:
        if self.access is None:
            require_admin_user(actor, self.admins)
            visible = self.store.list_workflow_definition_summaries()
        else:
            visible = [
                item for item in self.store.list_workflow_definition_summaries()
                if self.access.can_read(actor=actor, scope=ResourceScope.from_record(item))
            ]
        return [
            self._definition_summary_payload(item)
            for item in visible
        ]

    def get_definition(self, actor: str, workflow_key: str) -> dict[str, Any]:
        workflow = self._require_workflow_read(actor, workflow_key)
        return definition_payload(workflow)

    def restore_revision(self, actor: str, workflow_key: str, revision_no: int) -> dict[str, Any]:
        self._require_workflow_write(actor, workflow_key)
        with self.store.transaction():
            current = self.store.get_workflow_definition(workflow_key)
            if current is None:
                raise NotFound("workflow not found")
            revision = self.store.workflows.get_definition_revision(workflow_key, revision_no)
            if revision is None:
                raise NotFound("workflow revision not found")
            snapshot = revision.get("snapshot")
            if not isinstance(snapshot, dict) or snapshot.get("workflow_key") not in (None, workflow_key):
                raise ValidationError("workflow revision snapshot does not belong to this workflow")
            definition = snapshot.get("definition")
            if not isinstance(definition, dict):
                raise ValidationError("workflow revision snapshot has no valid definition")

            saved = self.upsert_definition(
                actor=actor,
                workflow_key=workflow_key,
                name=str(snapshot.get("name") or current.get("name") or workflow_key),
                description=str(snapshot.get("description") or ""),
                profile_key=str(snapshot.get("profile_key") or current.get("profile_key") or ""),
                status=str(snapshot.get("status") or current.get("status") or WorkflowStatus.active.value),
                workflow_type=str(snapshot.get("workflow_type") or current.get("workflow_type") or WorkflowType.operation.value),
                definition=definition,
                revision_source="restore",
            )
            saved["restored_from_revision"] = revision_no
            saved["revision_created"] = saved.get("revision_no") != current.get("current_revision_no")
            saved_revision = self.store.workflows.get_definition_revision(
                workflow_key, int(saved["revision_no"])
            )
            saved["revision_source"] = saved_revision.get("source") if saved_revision else "restore"
            return saved

    def export_definition(self, actor: str, workflow_key: str) -> dict[str, Any]:
        return self._imports.export_definition(actor, workflow_key)

    def preview_definition_import(
        self,
        *,
        actor: str,
        filename: str,
        content: bytes,
        target_workflow_key: str | None = None,
        target_mode: str = "auto",
    ) -> dict[str, Any]:
        return self._imports.preview_definition_import(
            actor=actor,
            filename=filename,
            content=content,
            target_workflow_key=target_workflow_key,
            target_mode=target_mode,
        )

    def confirm_definition_import(self, actor: str, import_id: str) -> dict[str, Any]:
        return self._imports.confirm_definition_import(actor, import_id)

    def delete_definition(self, actor: str, workflow_key: str) -> dict[str, Any]:
        self._require_workflow_write(actor, workflow_key)
        self.store.delete_workflow_definition(workflow_key)
        logger.info("Workflow 定义已删除 workflow=%s actor=%s", workflow_key, actor)
        return {"workflow_key": workflow_key, "deleted": True}

    def clear_execution_data(self, actor: str, workflow_key: str) -> dict[str, Any]:
        self._require_workflow_write(actor, workflow_key)
        counts = self.store.clear_workflow_execution_data(workflow_key)
        return {"workflow_key": workflow_key, "cleared": True, **counts}

    def list_runs(self, actor: str, workflow_key: str, *, limit: int = 20) -> list[dict[str, Any]]:
        if self.store.get_workflow_definition(workflow_key) is None and actor in self.admins:
            return []
        self._require_workflow_read(actor, workflow_key)
        bounded = min(max(limit, 1), 200)
        return self.store.list_workflow_runs(workflow_key, limit=bounded)

    def list_run_summaries(
        self,
        actor: str,
        workflow_key: str,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        self._require_workflow_read(actor, workflow_key)
        return self.store.list_workflow_run_summaries(
            workflow_key,
            limit=limit,
            offset=offset,
        )

    def list_run_overviews(self, actor: str) -> list[dict[str, Any]]:
        allowed = {item["workflow_key"] for item in self.list_definitions(actor)}
        return [
            item for item in self.store.list_workflow_run_overviews()
            if item.get("workflow_key") in allowed
        ]

    def list_completed_workflow_top(self, actor: str, *, limit: int = 5) -> dict[str, Any]:
        """返回上一个已结束工作流执行窗口内完成次数最多的工作流。"""
        allowed = {item["workflow_key"] for item in self.list_definitions(actor)}
        config = self.store.get_sync_config()
        now = local_now()
        period_start_local, period_end_local = previous_window_bounds(
            now=now,
            start=parse_hhmm(config.get("workflow_start_time")),
            stop=parse_hhmm(config.get("workflow_stop_time")),
        )
        return {
            "period_start": utc_iso(period_start_local),
            "period_end": utc_iso(period_end_local),
            "period_label": (
                f"{period_start_local:%m-%d %H:%M} → "
                f"{period_end_local:%m-%d %H:%M}"
            ),
            "items": [
                item for item in self.store.list_completed_workflow_top(
                period_start=utc_iso(period_start_local),
                period_end=utc_iso(period_end_local),
                limit=limit,
                ) if item.get("workflow_key") in allowed
            ],
        }

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
        self._require_workflow_read(actor, workflow_key)
        return {
            "tasks": self.store.list_workflow_tasks(
                workflow_key, status=status, type=type, search=search, sort=sort
            )
        }

    def refresh_tasks(
        self,
        *,
        actor: str,
        workflow_key: str,
        tasks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """显式将当前工作流中选定的完成任务放入增量刷新队列。"""
        self._require_workflow_write(actor, workflow_key)
        revision_no = self.store.workflows.get_current_definition_revision_no(workflow_key)
        revision = (
            self.store.workflows.get_definition_revision(workflow_key, revision_no)
            if revision_no > 0
            else None
        )
        content_hash = str((revision or {}).get("content_hash") or "")
        if not content_hash:
            raise ValidationError("workflow has no executable definition revision")
        refs = None
        if tasks is not None:
            refs = [
                (str(item.get("task_key") or ""), str(item.get("task_version") or ""))
                for item in tasks
            ]
            if any(not task_key for task_key, _ in refs):
                raise ValidationError("task_key is required")
        marked = self.store.workflows.mark_tasks_stale_for_refresh(
            workflow_key,
            content_hash,
            task_refs=refs,
        )
        logger.info(
            "Workflow 任务已请求增量刷新 workflow=%s revision=%s requested=%s marked=%s actor=%s",
            workflow_key,
            revision_no,
            len(refs) if refs is not None else "all",
            marked,
            actor,
        )
        return {
            "workflow_key": workflow_key,
            "revision_no": revision_no,
            "requested": len(refs) if refs is not None else None,
            "marked_stale": marked,
        }

    def preview_task_import(
        self,
        *,
        actor: str,
        workflow_key: str,
        filename: str,
        content: bytes,
    ) -> dict[str, Any]:
        self._require_workflow_write(actor, workflow_key)

        self.store.delete_expired_workflow_task_imports()
        try:
            parsed = parse_task_import(content, filename=filename)
        except TaskImportFormatError as exc:
            raise ValidationError(str(exc)) from exc

        valid_rows = [row for row in parsed.rows if not row.errors]
        tasks = [
            {
                "task_key": row.task_key,
                "task_version": row.task_version,
                "type": row.task_type,
                "payload": row.payload,
            }
            for row in valid_rows
        ]
        actions = self.store.preview_workflow_task_actions(workflow_key, tasks)
        action_by_key = {
            (row["task_key"], row["task_version"]): row for row in actions["rows"]
        }

        rows: list[dict[str, Any]] = []
        for parsed_row in parsed.rows:
            key = (parsed_row.task_key, parsed_row.task_version)
            if parsed_row.errors:
                rows.append(
                    {
                        "row_number": parsed_row.row_number,
                        "task_key": parsed_row.task_key,
                        "task_version": parsed_row.task_version,
                        "type": parsed_row.task_type,
                        "payload": parsed_row.payload,
                        "action": "error",
                        "errors": list(parsed_row.errors),
                    }
                )
                continue
            action_row = action_by_key[key]
            rows.append(
                {
                    "row_number": parsed_row.row_number,
                    "task_key": parsed_row.task_key,
                    "task_version": parsed_row.task_version,
                    "type": parsed_row.task_type,
                    "payload": action_row["payload"],
                    "action": action_row["action"],
                    "errors": [],
                }
            )

        invalid_row_count = len(parsed.rows) - len(valid_rows)
        can_confirm = bool(valid_rows) and invalid_row_count == 0
        summary = {
            "total_rows": len(parsed.rows),
            "valid_rows": len(valid_rows),
            "invalid_rows": invalid_row_count,
            **actions["summary"],
        }
        import_id = f"task_import_{uuid.uuid4().hex}"
        expires_at = utc_now() + timedelta(minutes=30)
        report = {
            "import_id": import_id,
            "filename": parsed.filename,
            "sheet_name": parsed.sheet_name,
            "expires_at": utc_iso(expires_at),
            "can_confirm": can_confirm,
            "summary": summary,
            "rows": rows,
        }
        self.store.create_workflow_task_import(
            import_id=import_id,
            workflow_key=workflow_key,
            actor=actor,
            filename=parsed.filename,
            sheet_name=parsed.sheet_name,
            tasks=tasks,
            preview=report,
            expires_at=expires_at,
        )
        return report

    def confirm_task_import(
        self,
        *,
        actor: str,
        workflow_key: str,
        import_id: str,
    ) -> dict[str, Any]:
        self._require_workflow_write(actor, workflow_key)
        snapshot = self.store.get_workflow_task_import(import_id)
        if snapshot is None:
            raise NotFound("workflow task import not found")
        if not (snapshot.get("preview") or {}).get("can_confirm", True):
            raise ValidationError("workflow task import cannot be confirmed")
        try:
            return self.store.confirm_workflow_task_import(
                workflow_key,
                import_id=import_id,
                actor=actor,
            )
        except KeyError as exc:
            raise NotFound("workflow task import not found") from exc
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

    def build_task_import_template(
        self,
        *,
        actor: str,
        workflow_key: str,
    ) -> bytes:
        self._require_workflow_read(actor, workflow_key)
        return build_task_import_template_file()

    @staticmethod
    def _is_leasable(task: dict[str, Any]) -> bool:
        """Mirror of lease_workflow_task's eligibility: pending, or running
        with an expired lease."""
        status = task.get("status")
        if status in {"pending", "stale"}:
            return True
        if status == "running":
            expires_at = task.get("lease_expires_at")
            if expires_at:
                parsed_expires_at = parse_utc(expires_at)
                return parsed_expires_at is not None and parsed_expires_at < utc_now()
        return False

    @staticmethod
    def resolve_execution_mode(*, task_status: Any, requested_mode: str) -> str:
        """将 stale 任务的默认普通执行升级为增量执行。"""
        if task_status == "stale" and requested_mode == "normal":
            return "incremental"
        return requested_mode

    def execute_task(
        self,
        *,
        actor: str,
        workflow_key: str,
        task_key: str,
        task_version: str | None = None,
        execution_mode: str = "normal",
    ) -> dict[str, Any]:
        """Mark a single task for priority execution.

        Stamps a one-shot priority flag so the next ``workflow_get_task`` lease
        picks this task ahead of normal id ordering, then (in the route layer)
        a workflow run is started. Does not itself start the run. Stale tasks
        default to incremental execution. A completed task from the current
        revision is deliberately non-rerunnable unless the caller explicitly
        chooses ``force_full``.
        """
        self._require_workflow_write(actor, workflow_key)
        if execution_mode not in {"normal", "incremental", "force_full"}:
            raise ValidationError(f"unsupported workflow execution mode: {execution_mode}")
        if self.store.get_workflow_definition(workflow_key) is None:
            raise NotFound("workflow not found")
        task = self.store.get_workflow_task(workflow_key, task_key, task_version=(task_version or None))
        if task is None:
            raise NotFound("workflow task not found")
        effective_mode = self.resolve_execution_mode(
            task_status=task.get("status"), requested_mode=execution_mode
        )
        if task.get("status") == "completed":
            if execution_mode == "incremental":
                revision_no = self.store.workflows.get_current_definition_revision_no(workflow_key)
                revision = self.store.workflows.get_definition_revision(workflow_key, revision_no)
                content_hash = str((revision or {}).get("content_hash") or "")
                marked = self.store.workflows.mark_tasks_stale_for_refresh(
                    workflow_key,
                    content_hash,
                    task_refs=[(task_key, str(task.get("task_version") or ""))],
                ) if content_hash else 0
                if not marked:
                    raise ValidationError(
                        "completed task does not need incremental refresh; use execution_mode=force_full"
                    )
                task = self.store.get_workflow_task(
                    workflow_key, task_key, task_version=str(task.get("task_version") or "")
                )
                if task is None:
                    raise NotFound("workflow task not found")
            elif execution_mode != "force_full":
                # Keep the historical validation response for callers that do
                # not explicitly opt into a full rerun. The UI sends
                # force_full for completed tasks with existing output.
                raise ValidationError("completed task requires execution_mode=force_full")
            elif execution_mode == "force_full":
                resolved_task_version = str(task.get("task_version") or "")
                if not self.store.reset_workflow_task(
                    workflow_key, task_key, task_version=resolved_task_version
                ):
                    raise NotFound("workflow task not found")
                task = self.store.get_workflow_task(
                    workflow_key, task_key, task_version=resolved_task_version
                )
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
            "execution_mode": effective_mode,
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
        change queue ordering. ``attempt_count`` and the pending failure reason
        are kept while waiting for retry; a successful retry clears the task's
        ``last_error``. Detailed historical errors remain on workflow runs.
        """
        self._require_workflow_write(actor, workflow_key)
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
        run = self._require_run_read(actor, run_id)
        run["node_runs"] = self.store.list_workflow_node_runs(run_id)
        return run

    def require_run_write(self, actor: str, run_id: str) -> dict[str, Any]:
        run = self._require_run_read(actor, run_id)
        self._require_workflow_write(actor, str(run["workflow_key"]))
        return run

    def preview_incremental_run(
        self,
        *,
        actor: str,
        workflow_key: str,
        task_key: str,
        task_version: str | None = None,
        execution_mode: str = "normal",
    ) -> dict[str, Any]:
        """Build the exact reuse plan without leasing a task or creating a run."""
        if execution_mode not in {"normal", "incremental", "force_full"}:
            raise ValidationError(f"unsupported workflow execution mode: {execution_mode}")
        effective_mode = execution_mode
        if task_key is not None:
            task = self.store.get_workflow_task(workflow_key, task_key, task_version=task_version)
            if task is None:
                raise NotFound("workflow task not found")
            task_version = str(task.get("task_version") or "")
            effective_mode = self.resolve_execution_mode(
                task_status=task.get("status"), requested_mode=execution_mode
            )
        return incremental_plan_preview_payload(
            self.build_incremental_plan(
                actor=actor,
                workflow_key=workflow_key,
                task_key=task_key,
                task_version=task_version,
                execution_mode=effective_mode,
            )
        )

    def build_incremental_plan(
        self,
        *,
        actor: str,
        workflow_key: str,
        task_key: str | None = None,
        task_version: str | None = None,
        execution_mode: str = "normal",
        workflow: dict[str, Any] | None = None,
        definition: dict[str, Any] | WorkflowGraph | None = None,
    ) -> IncrementalPlan:
        """Resolve the current revision, task and selected baseline run.

        Thin coordinator over :func:`incremental.build_incremental_plan`; the
        planning logic lives in the ``incremental`` module so this service stays
        focused on orchestration.
        """
        self._require_workflow_read(actor, workflow_key)
        return _build_incremental_plan(
            store=self.store,
            validator=self.validator,
            actor=actor,
            workflow_key=workflow_key,
            task_key=task_key,
            task_version=task_version,
            execution_mode=execution_mode,
            workflow=workflow,
            definition=definition,
        )

    @staticmethod
    def incremental_plan_payload(plan: IncrementalPlan) -> dict[str, Any]:
        return incremental_plan_payload(plan)

    @staticmethod
    def incremental_plan_preview_payload(plan: IncrementalPlan) -> dict[str, Any]:
        """Public, non-artifact-bearing execution-plan representation."""
        return incremental_plan_preview_payload(plan)

    def workflow_run_start_payload(self, started: dict[str, Any]) -> dict[str, Any]:
        """Return the stable API response for a synchronously-created run."""
        run_id = str(started.get("run_id") or "")
        run = self.store.get_workflow_run(run_id) if run_id else None
        execution_mode = str((run or {}).get("execution_mode") or "normal")
        plan = incremental_plan_from_payload((run or {}).get("execution_plan") or {})
        return {
            "run_id": run_id,
            "run_status": str(started.get("status") or (run or {}).get("status") or "started"),
            "execution_mode": execution_mode,
            "plan": incremental_plan_preview_payload(plan),
        }

    @staticmethod
    def incremental_plan_from_payload(payload: dict[str, Any]) -> IncrementalPlan:
        return incremental_plan_from_payload(payload)

    @staticmethod
    def _definition_summary_payload(workflow: dict[str, Any]) -> dict[str, Any]:
        payload = definition_payload(workflow)
        payload["definition"] = None
        return payload

    # --- versioning & diff -----------------------------------------------

    def list_revisions(self, actor: str, workflow_key: str, *, limit: int = 100) -> list[dict[str, Any]]:
        self._require_workflow_read(actor, workflow_key)
        revisions = self.store.workflows.list_definition_revisions(workflow_key, limit=limit)
        current = self.store.workflows.get_current_definition_revision_no(workflow_key)
        for rev in revisions:
            rev["is_current"] = rev.get("revision_no") == current
        return revisions

    def get_revision(self, actor: str, workflow_key: str, revision_no: int) -> dict[str, Any]:
        self._require_workflow_read(actor, workflow_key)
        revision = self.store.workflows.get_definition_revision(workflow_key, revision_no)
        if revision is None:
            raise NotFound("workflow revision not found")
        current = self.store.workflows.get_current_definition_revision_no(workflow_key)
        revision["is_current"] = revision.get("revision_no") == current
        return revision

    def diff_revisions(
        self, actor: str, workflow_key: str, *, from_no: int | None, to_no: int | None
    ) -> dict[str, Any]:
        self._require_workflow_read(actor, workflow_key)
        current = self.store.workflows.get_current_definition_revision_no(workflow_key)
        if current == 0:
            raise NotFound("workflow has no revisions")
        to_revision_no = to_no if to_no is not None else current
        from_revision_no = from_no if from_no is not None else max(to_revision_no - 1, 1)
        to_rev = self.store.workflows.get_definition_revision(workflow_key, to_revision_no)
        from_rev = self.store.workflows.get_definition_revision(workflow_key, from_revision_no)
        if to_rev is None:
            raise NotFound(f"workflow revision {to_revision_no} not found")
        if from_rev is None:
            raise NotFound(f"workflow revision {from_revision_no} not found")
        from_snapshot = from_rev.get("snapshot") or {}
        to_snapshot = to_rev.get("snapshot") or {}
        from_text = json.dumps(from_snapshot, ensure_ascii=False, indent=2, sort_keys=True)
        to_text = json.dumps(to_snapshot, ensure_ascii=False, indent=2, sort_keys=True)
        return {
            "entity_type": "workflow",
            "entity_key": workflow_key,
            "from_revision": from_revision_no,
            "to_revision": to_revision_no,
            "text": text_diff(
                from_text,
                to_text,
                from_label=f"revision {from_revision_no}",
                to_label=f"revision {to_revision_no}",
            ),
            "structured": workflow_structured_diff(from_snapshot, to_snapshot),
        }

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
        self._require_run_read(actor, run_id)
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
        _workflow, run = self.require_workflow_run_context(
            profile_key=profile_key, workflow_key=workflow_key, run_id=run_id
        )
        selected_task_key = run.get("task_key")
        selected_task_version = str(run.get("task_version") or "")
        if selected_task_key is not None:
            selected = self.store.get_workflow_task(
                workflow_key, str(selected_task_key), task_version=selected_task_version
            )
            if (
                selected is not None
                and selected.get("status") == "running"
                and selected.get("lease_run_id") == run_id
            ):
                # 按需运行会在启动执行线程前租赁指定任务。节点仍需执行以
                # 完整记录时间轴，但必须返回该租约，不能领取另一条队列任务。
                return {"task": selected}
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
        logger.info(
            "workflow_set_task 收到任务批次 workflow=%s run=%s requested=%d",
            workflow_key,
            run_id,
            len(tasks),
        )
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

        task_key_counts = Counter(task["task_key"] for task in normalized)
        task_pair_counts = Counter((task["task_key"], task["task_version"]) for task in normalized)
        duplicate_task_key_rows = sum(count - 1 for count in task_key_counts.values() if count > 1)
        duplicate_task_pair_rows = sum(count - 1 for count in task_pair_counts.values() if count > 1)
        batch_diagnostics = {
            "unique_task_keys": len(task_key_counts),
            "unique_task_pairs": len(task_pair_counts),
            "duplicate_task_key_rows": duplicate_task_key_rows,
            "duplicate_task_pair_rows": duplicate_task_pair_rows,
            "empty_task_version_count": sum(not task["task_version"] for task in normalized),
        }
        logger.info(
            "workflow_set_task 批次归一化 workflow=%s run=%s received=%d unique_keys=%d unique_pairs=%d duplicate_key_rows=%d duplicate_pair_rows=%d empty_versions=%d",
            workflow_key,
            run_id,
            len(normalized),
            batch_diagnostics["unique_task_keys"],
            batch_diagnostics["unique_task_pairs"],
            batch_diagnostics["duplicate_task_key_rows"],
            batch_diagnostics["duplicate_task_pair_rows"],
            batch_diagnostics["empty_task_version_count"],
        )
        result = self.store.upsert_workflow_tasks(workflow_key, normalized)
        action_total = sum(
            result.get(action, 0)
            for action in (
                "created",
                "updated",
                "skipped_completed",
                "skipped_running",
                "skipped_historical",
                "reopened_expired",
            )
        )
        if action_total != len(normalized):
            logger.warning(
                "workflow_set_task 批次处理数量不一致 workflow=%s run=%s received=%d action_total=%d result=%s",
                workflow_key,
                run_id,
                len(normalized),
                action_total,
                result,
            )
        logger.info(
            "workflow_set_task 写入任务批次 workflow=%s run=%s received=%d action_total=%d created=%d updated=%d skipped_completed=%d skipped_running=%d skipped_historical=%d reopened_expired=%d superseded=%d",
            workflow_key,
            run_id,
            len(normalized),
            action_total,
            result.get("created", 0),
            result.get("updated", 0),
            result.get("skipped_completed", 0),
            result.get("skipped_running", 0),
            result.get("skipped_historical", 0),
            result.get("reopened_expired", 0),
            result.get("superseded", 0),
        )
        return {
            "workflow_key": workflow_key,
            "run_id": run_id,
            "received": len(normalized),
            "action_total": action_total,
            **batch_diagnostics,
            **result,
        }

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
        producer_node_id: str | None = None,
        producer_node_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        return self._artifacts.save_artifact(
            workflow_key=workflow_key,
            profile_key=profile_key,
            run_id=run_id,
            task_key=task_key,
            title=title,
            path=path,
            tags=tags,
            format=format,
            summary=summary,
            content=content,
            metadata=metadata,
            task_version=task_version,
            producer_node_id=producer_node_id,
            producer_node_fingerprint=producer_node_fingerprint,
        )

    def ingest_parsed_result(
        self,
        *,
        workflow_key: str,
        profile_key: str,
        run_id: str,
        parsed: ParsedWorkflowResult,
    ) -> dict[str, Any]:
        return self._artifacts.ingest_parsed_result(
            workflow_key=workflow_key,
            profile_key=profile_key,
            run_id=run_id,
            parsed=parsed,
        )

    def generate_html_report_for_run(
        self,
        *,
        workflow_key: str,
        profile_key: str,
        run_id: str,
        actor: str,
    ) -> dict[str, Any]:
        """Generate a human-readable HTML report for a completed summary run.

        Thin coordinator over :func:`render_run_html_report`; the rendering
        logic lives in ``report_renderer`` so this service stays focused on
        orchestration. Failures must NOT alter the main workflow run status —
        callers (the scheduler) wrap this in try/except and log a warning.
        """
        return render_run_html_report(
            store=self.store,
            agent_service=self.agent_service,
            skills=self.skills,
            save_artifact=self.save_artifact,
            workflow_key=workflow_key,
            profile_key=profile_key,
            run_id=run_id,
            actor=actor,
        )

    def get_artifact(
        self,
        *,
        actor: str,
        artifact_id: str,
        profile_key: str | None = None,
        trusted_profile_context: bool = False,
    ) -> dict[str, Any]:
        return self._artifacts.get_artifact(
            actor=actor,
            artifact_id=artifact_id,
            profile_key=profile_key,
            trusted_profile_context=trusted_profile_context,
        )

    def set_artifact_visibility(
        self, *, actor: str, artifact_id: str, visibility: str
    ) -> dict[str, Any]:
        return self._artifacts.set_visibility(
            actor=actor, artifact_id=artifact_id, visibility=visibility
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
        offset: int = 0,
        task_key: str | None = None,
        task_version: str | None = None,
        run_id: str | None = None,
        include_history: bool = False,
        trusted_profile_context: bool = False,
        full: bool = False,
        format: str | None = None,
        paginated: bool = False,
        path_match: str | None = None,
    ) -> dict[str, Any]:
        return self._artifacts.search_artifacts(
            actor=actor,
            profile_key=profile_key,
            query=query,
            tags=tags,
            path=path,
            workflow_key=workflow_key,
            limit=limit,
            offset=offset,
            task_key=task_key,
            task_version=task_version,
            run_id=run_id,
            include_history=include_history,
            trusted_profile_context=trusted_profile_context,
            full=full,
            format=format,
            paginated=paginated,
            path_match=path_match,
        )

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
        return self._artifacts.list_artifact_history(
            actor=actor,
            profile_key=profile_key,
            workflow_key=workflow_key,
            task_key=task_key,
            limit=limit,
            trusted_profile_context=trusted_profile_context,
        )

    def _require_workflow_create(self, actor: str, profile_key: str) -> str:
        if self.access is None:
            require_admin_user(actor, self.admins)
            return ""
        actor_group_key = str(self.access.actor_group_key(actor, required=True))
        try:
            profile = self._require_profile_same_group(actor, profile_key, actor_group_key)
        except NotFound as exc:
            raise WorkflowDefinitionValidationError(
                [
                    WorkflowValidationIssue(
                        scope="workflow",
                        id=None,
                        field="profile_key",
                        code="not_found",
                        message="能力平面不存在",
                    )
                ]
            ) from exc
        return str(profile.get("owner_group_key") or actor_group_key)

    def _require_profile_same_group(
        self, actor: str, profile_key: str, owner_group_key: str
    ) -> dict[str, Any]:
        if self.access is None:
            profile = self.store.get_project_profile(profile_key)
            if profile is None:
                raise NotFound("profile not found")
            return profile
        profile = self.access.require_resource_read(
            actor=actor,
            resource_type=ScopedResourceType.capability_profile,
            resource_key=profile_key,
        )
        profile_group_key = str(profile.get("owner_group_key") or "")
        if profile_group_key and profile_group_key != owner_group_key and actor not in self.admins:
            raise ValidationError("工作流只能使用同一数据组的能力平面")
        return profile

    def _require_workflow_read(self, actor: str, workflow_key: str) -> dict[str, Any]:
        workflow = self.store.get_workflow_definition(workflow_key)
        if workflow is None:
            raise NotFound("workflow not found")
        if self.access is None:
            require_admin_user(actor, self.admins)
        else:
            self.access.require_read(actor=actor, scope=ResourceScope.from_record(workflow))
        return workflow

    def _require_workflow_write(self, actor: str, workflow_key: str) -> dict[str, Any]:
        workflow = self.store.get_workflow_definition(workflow_key)
        if workflow is None:
            raise NotFound("workflow not found")
        if self.access is None:
            require_admin_user(actor, self.admins)
        else:
            self.access.require_write(actor=actor, scope=ResourceScope.from_record(workflow))
        return workflow

    def _require_run_read(self, actor: str, run_id: str) -> dict[str, Any]:
        run = self.store.get_workflow_run(run_id)
        if run is None:
            raise NotFound("workflow run not found")
        if self.access is None:
            require_admin_user(actor, self.admins)
        else:
            self.access.require_read(
                actor=actor,
                scope=ResourceScope.from_record(
                    {"owner_group_key": run.get("owner_group_key"), "visibility": "group"}
                ),
            )
        return run
