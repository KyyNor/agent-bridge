from __future__ import annotations

from collections import Counter
import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from agent_bridge.core.domain import AccessDenied, ConflictError, NotFound, ValidationError, require_admin_user
from agent_bridge.core.diff import text_diff, workflow_structured_diff
from agent_bridge.storage.sqlite import SQLiteStore
from agent_bridge.automation.workflows.models import (
    WorkflowArtifactFormat,
    WorkflowStatus,
    WorkflowType,
)
from agent_bridge.automation.workflows.task_import import (
    TaskImportFormatError,
    build_task_import_template as build_task_import_template_file,
    parse_task_import,
)
from agent_bridge.automation.workflows.definition import WorkflowGraph, normalize_summary_graph
from agent_bridge.automation.workflows.incremental import (
    IncrementalPlan,
    NodePlan,
    WorkflowIncrementalPlanner,
)
from agent_bridge.automation.workflows.validator import WorkflowValidator

logger = logging.getLogger(__name__)

WORKFLOW_REVISION_SOURCES = frozenset({"edit", "import", "restore"})
WORKFLOW_IMPORT_MODES = frozenset({"auto", "new", "overwrite"})


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
        scripts: Any = None,
    ) -> None:
        self.store = store
        self.admins = admins
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
        workflow_js: str = "",
        revision_source: str = "edit",
    ) -> dict[str, Any]:
        # Kept only for internal callers that still construct historical test
        # fixtures. New API schemas do not expose or execute this field.
        del workflow_js
        require_admin_user(actor, self.admins)
        if revision_source not in WORKFLOW_REVISION_SOURCES:
            raise ValidationError(f"unsupported workflow revision source: {revision_source}")
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
        new_content_hash = self._workflow_content_hash(
            graph_payload, name, description, profile_key, next_status, next_type
        )
        with self.store.transaction():
            previous_revisions = self.store.workflows.list_definition_revisions(workflow_key, limit=1)
            previous_hash = previous_revisions[0]["content_hash"] if previous_revisions else None
            result = self.store.upsert_workflow_definition(
                workflow_key=workflow_key,
                name=name,
                description=description,
                profile_key=profile_key,
                definition=graph_payload,
                workflow_js="",
                status=next_status,
                workflow_type=next_type,
                created_by=actor,
            )
            # Archive a revision whenever execution semantics changed (or on
            # the first save, including an upgraded legacy database).
            content_changed = not previous_revisions or previous_hash != new_content_hash
            revision_no = previous_revisions[0]["revision_no"] if previous_revisions else 0
            if content_changed:
                revision = self.store.workflows.create_definition_revision(
                    workflow_key=workflow_key,
                    content_hash=new_content_hash,
                    snapshot=self._workflow_revision_snapshot(result),
                    actor=actor,
                    source=revision_source,
                )
                revision_no = revision["revision_no"]
                self.store.workflows.mark_latest_task_stale_if_needed(
                    workflow_key, revision_no, new_content_hash
                )
        result["content_hash"] = new_content_hash
        result["revision_no"] = revision_no
        logger.info(
            "Workflow 定义已保存 workflow=%s profile=%s 状态=%s 类型=%s actor=%s",
            workflow_key,
            profile_key,
            next_status,
            next_type,
            actor,
        )
        return self._definition_payload(result)

    def list_definitions(self, actor: str) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        return [self._definition_payload(item) for item in self.store.list_workflow_definitions()]

    def get_definition(self, actor: str, workflow_key: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        workflow = self.store.get_workflow_definition(workflow_key)
        if workflow is None:
            raise NotFound("workflow not found")
        return self._definition_payload(workflow)

    def restore_revision(self, actor: str, workflow_key: str, revision_no: int) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
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
        require_admin_user(actor, self.admins)
        with self.store.transaction():
            workflow = self.store.get_workflow_definition(workflow_key)
            if workflow is None:
                raise NotFound("workflow not found")
            public = self._definition_payload(workflow)
            if not isinstance(public.get("definition"), dict):
                raise NotFound("workflow has no exportable definition")
            current_revision_no = int(workflow.get("current_revision_no") or 0)
            revision = (
                self.store.workflows.get_definition_revision(workflow_key, current_revision_no)
                if current_revision_no > 0
                else None
            )
            if revision is None:
                content_hash = self._workflow_content_hash(
                    public["definition"],
                    str(public.get("name") or workflow_key),
                    str(public.get("description") or ""),
                    str(public.get("profile_key") or ""),
                    str(public.get("status") or WorkflowStatus.active.value),
                    str(public.get("workflow_type") or WorkflowType.operation.value),
                )
                latest = self.store.workflows.list_definition_revisions(workflow_key, limit=1)
                if latest and latest[0].get("content_hash") == content_hash:
                    self.store.workflows.set_current_definition_revision_no(
                        workflow_key, int(latest[0]["revision_no"])
                    )
                    revision = self.store.workflows.get_definition_revision(
                        workflow_key, int(latest[0]["revision_no"])
                    )
                else:
                    revision = self.store.workflows.create_definition_revision(
                        workflow_key=workflow_key,
                        content_hash=content_hash,
                        snapshot=self._workflow_revision_snapshot(public),
                        actor=actor,
                        source="edit",
                    )
            if revision is None:
                raise NotFound("workflow revision not found")
            return {
                "format": "agent-bridge.workflow",
                "format_version": 1,
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "exported_by": actor,
                "workflow": {
                    key: public[key]
                    for key in (
                        "workflow_key", "name", "description", "profile_key",
                        "status", "workflow_type", "definition",
                    )
                },
                "revision": {
                    key: revision[key]
                    for key in ("revision_no", "content_hash", "source", "created_by", "created_at")
                },
            }

    def preview_definition_import(
        self,
        *,
        actor: str,
        filename: str,
        content: bytes,
        target_workflow_key: str | None = None,
        target_mode: str = "auto",
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        if target_mode not in WORKFLOW_IMPORT_MODES:
            raise ValidationError("导入方式不正确，请选择自动判断、导入为新工作流或覆盖现有工作流")
        try:
            envelope = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError("工作流文件不是有效的 UTF-8 JSON，请重新导出或检查文件内容") from exc
        if not isinstance(envelope, dict):
            raise ValidationError("工作流文件格式不正确，请使用系统导出的工作流 JSON 文件")
        if envelope.get("format") != "agent-bridge.workflow":
            raise ValidationError("不支持的工作流文件格式，请使用系统导出的工作流 JSON 文件")
        if envelope.get("format_version") != 1:
            raise ValidationError("不支持的工作流文件版本，请重新导出当前版本的工作流")

        source = envelope.get("workflow")
        if not isinstance(source, dict):
            raise ValidationError("工作流文件缺少 workflow 内容，请使用系统导出的工作流 JSON 文件")
        source_workflow_key = str(source.get("workflow_key") or "").strip()
        target_key = str(target_workflow_key or source_workflow_key).strip()
        if not source_workflow_key or not target_key:
            raise ValidationError("工作流文件缺少 workflow key，请补充后重试")
        try:
            status = WorkflowStatus(str(source.get("status") or WorkflowStatus.active.value)).value
            workflow_type = WorkflowType(
                str(source.get("workflow_type") or WorkflowType.operation.value)
            ).value
        except ValueError as exc:
            raise ValidationError("工作流状态或类型不正确，请重新导出当前版本的工作流") from exc
        imported = {
            "workflow_key": target_key,
            "name": str(source.get("name") or target_key),
            "description": str(source.get("description") or ""),
            "profile_key": str(source.get("profile_key") or ""),
            "status": status,
            "workflow_type": workflow_type,
            "definition": source.get("definition"),
        }
        if not imported["profile_key"] or not isinstance(imported["definition"], dict):
            raise ValidationError("工作流文件缺少能力平面或 definition 内容，请检查后重试")
        if workflow_type == WorkflowType.summary.value:
            try:
                source_graph = WorkflowGraph.model_validate(imported["definition"])
            except PydanticValidationError:
                self.validator.require_valid(actor=actor, workflow=imported)
                raise ValidationError("工作流定义校验失败，请检查节点配置")
            default_backend = next(
                (
                    node.config.backend_key
                    for node in source_graph.nodes
                    if node.type in {"agent", "output"} and node.config.backend_key
                ),
                "claude",
            )
            imported["definition"] = normalize_summary_graph(source_graph, default_backend).model_dump(mode="json")
        graph = self.validator.require_valid(actor=actor, workflow=imported)
        imported["definition"] = graph.model_dump(mode="json")

        existing = self.store.get_workflow_definition(target_key)
        if target_mode == "new" and existing is not None:
            raise ConflictError(
                f"目标 workflow key「{target_key}」已经存在，请更换 key 或选择覆盖现有工作流"
            )
        if target_mode == "overwrite" and existing is None:
            raise ConflictError(f"找不到要覆盖的工作流「{target_key}」，请刷新后重新预览")
        operation = "overwrite" if existing is not None else "create"
        target_revision_no = int(existing.get("current_revision_no") or 0) if existing else 0
        diff = None
        if existing is not None:
            target_revision = (
                self.store.workflows.get_definition_revision(target_key, target_revision_no)
                if target_revision_no > 0
                else None
            )
            before = (
                target_revision["snapshot"]
                if target_revision is not None
                else self._workflow_revision_snapshot(self._definition_payload(existing))
            )
            after = self._workflow_revision_snapshot(imported)
            before_text = json.dumps(before, ensure_ascii=False, indent=2, sort_keys=True)
            after_text = json.dumps(after, ensure_ascii=False, indent=2, sort_keys=True)
            diff = {
                "entity_type": "workflow",
                "entity_key": target_key,
                "from_revision": target_revision_no,
                "to_revision": None,
                "text": text_diff(
                    before_text,
                    after_text,
                    from_label=f"current v{target_revision_no}",
                    to_label="imported definition",
                ),
                "structured": workflow_structured_diff(before, after),
            }

        import_id = f"workflow_import_{uuid.uuid4().hex}"
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
        self.store.workflows.delete_expired_workflow_definition_imports()
        self.store.workflows.create_workflow_definition_import(
            import_id=import_id,
            actor=actor,
            filename=filename or "workflow.workflow.json",
            source_workflow_key=source_workflow_key,
            target_workflow_key=target_key,
            operation=operation,
            workflow=imported,
            target_revision_no=target_revision_no,
            expires_at=expires_at,
        )
        return {
            "import_id": import_id,
            "filename": filename or "workflow.workflow.json",
            "expires_at": expires_at.isoformat(),
            "source_workflow_key": source_workflow_key,
            "target_workflow_key": target_key,
            "operation": operation,
            "target_revision_no": target_revision_no,
            "can_confirm": True,
            "workflow": imported,
            "diff": diff,
        }

    def confirm_definition_import(self, actor: str, import_id: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        now = datetime.now(timezone.utc)
        with self.store.transaction():
            snapshot = self.store.workflows.get_workflow_definition_import(import_id)
            if snapshot is None:
                raise NotFound("workflow definition import not found")
            if snapshot.get("actor") != actor:
                raise AccessDenied("workflow definition import belongs to another actor")
            if snapshot.get("status") != "previewed":
                raise ConflictError("导入预览已失效，请重新选择文件并预览")
            try:
                expires_at = datetime.fromisoformat(str(snapshot["expires_at"]).replace("Z", "+00:00"))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValidationError("workflow definition import expiry is invalid") from exc
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= now:
                raise ValidationError("workflow definition import expired")

            target_key = str(snapshot.get("target_workflow_key") or "")
            imported = snapshot.get("workflow")
            if not target_key or not isinstance(imported, dict):
                raise ValidationError("workflow definition import session is invalid")
            current = self.store.get_workflow_definition(target_key)
            current_revision_no = int(current.get("current_revision_no") or 0) if current else 0
            expected_revision_no = int(snapshot.get("target_revision_no") or 0)
            if current_revision_no != expected_revision_no:
                raise ConflictError("工作流在预览后发生了变化，请重新预览")
            if snapshot.get("operation") == "overwrite" and current is None:
                raise ConflictError("要覆盖的工作流已不存在，请重新预览")
            if snapshot.get("operation") == "create" and current is not None:
                raise ConflictError("目标 workflow key 已被其他操作占用，请重新预览")

            graph = self.validator.require_valid(actor=actor, workflow=imported)
            saved = self.upsert_definition(
                actor=actor,
                workflow_key=target_key,
                name=str(imported.get("name") or target_key),
                description=str(imported.get("description") or ""),
                profile_key=str(imported.get("profile_key") or ""),
                status=str(imported.get("status") or WorkflowStatus.active.value),
                workflow_type=str(imported.get("workflow_type") or WorkflowType.operation.value),
                definition=graph,
                revision_source="import",
            )
            if not self.store.workflows.confirm_workflow_definition_import(import_id, actor=actor):
                raise ConflictError("导入预览已失效，请重新选择文件并预览")
            saved_revision = self.store.workflows.get_definition_revision(
                target_key, int(saved["revision_no"])
            )
            saved["revision_source"] = saved_revision.get("source") if saved_revision else "import"
            self.store.workflows.delete_workflow_definition_import(import_id, actor=actor)
            saved["import_id"] = import_id
            saved["operation"] = snapshot.get("operation")
            return saved

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

    def preview_task_import(
        self,
        *,
        actor: str,
        workflow_key: str,
        filename: str,
        content: bytes,
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        if self.store.get_workflow_definition(workflow_key) is None:
            raise NotFound("workflow not found")

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
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=30)
        report = {
            "import_id": import_id,
            "filename": parsed.filename,
            "sheet_name": parsed.sheet_name,
            "expires_at": expires_at.isoformat(),
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
        require_admin_user(actor, self.admins)
        if self.store.get_workflow_definition(workflow_key) is None:
            raise NotFound("workflow not found")
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
        require_admin_user(actor, self.admins)
        if self.store.get_workflow_definition(workflow_key) is None:
            raise NotFound("workflow not found")
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
        require_admin_user(actor, self.admins)
        if execution_mode not in {"normal", "incremental", "force_full"}:
            raise ValidationError(f"unsupported workflow execution mode: {execution_mode}")
        if self.store.get_workflow_definition(workflow_key) is None:
            raise NotFound("workflow not found")
        task = self.store.get_workflow_task(workflow_key, task_key, task_version=(task_version or None))
        if task is None:
            raise NotFound("workflow task not found")
        effective_mode = "incremental" if task.get("status") == "stale" and execution_mode == "normal" else execution_mode
        if task.get("status") == "completed":
            if execution_mode != "force_full":
                # Keep the historical validation response for callers that do
                # not explicitly opt into a full rerun. The UI sends
                # force_full for completed tasks with existing output.
                raise ValidationError("completed task requires execution_mode=force_full")
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
        run["node_runs"] = self.store.list_workflow_node_runs(run_id)
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
            if task.get("status") == "stale" and execution_mode == "normal":
                effective_mode = "incremental"
        return self.incremental_plan_preview_payload(
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
        """Resolve the current revision, task and complete baseline runs.

        The planner itself remains storage-agnostic; this method is the single
        service boundary that supplies revision-scoped persistence data.
        """
        if execution_mode not in {"normal", "incremental", "force_full"}:
            raise ValidationError(f"unsupported workflow execution mode: {execution_mode}")
        workflow = workflow or self.store.get_workflow_definition(workflow_key)
        if workflow is None:
            raise NotFound("workflow not found")
        graph_payload = definition or workflow.get("definition")
        if not isinstance(graph_payload, WorkflowGraph):
            if not isinstance(graph_payload, dict):
                raise ValidationError("workflow definition is required")
            graph = WorkflowGraph.model_validate(graph_payload)
        else:
            graph = graph_payload
        current_revision_no = self.store.workflows.get_current_definition_revision_no(workflow_key)
        current_revision = (
            self.store.workflows.get_definition_revision(workflow_key, current_revision_no)
            if current_revision_no > 0
            else None
        )
        resolved_task: dict[str, Any]
        if task_key is None:
            resolved_task = {"task_key": "", "task_version": ""}
        else:
            resolved_task = self.store.get_workflow_task(
                workflow_key, task_key, task_version=task_version
            ) or {}
            if not resolved_task:
                raise NotFound("workflow task not found")
        resolved_version = str(resolved_task.get("task_version") or "")
        candidates = (
            self.store.workflows.list_completed_workflow_runs_for_task(
                workflow_key, str(resolved_task.get("task_key") or ""), resolved_version
            )
            if resolved_task.get("task_key")
            else []
        )
        usable_runs: list[dict[str, Any]] = []
        node_runs_by_id: dict[str, list[dict[str, Any]]] = {}
        artifacts_by_id: dict[str, list[dict[str, Any]]] = {}
        current_node_ids = {node.id for node in graph.nodes}
        for run in candidates:
            run_id = str(run["run_id"])
            node_runs = self.store.list_workflow_node_runs(run_id)
            rows_by_node = {str(row.get("node_id")): row for row in node_runs}
            if any(
                node_id not in rows_by_node
                or rows_by_node[node_id].get("status") != "completed"
                for node_id in current_node_ids
            ):
                continue
            usable_runs.append(run)
            node_runs_by_id[run_id] = node_runs
            artifacts: list[dict[str, Any]] = []
            for row in node_runs:
                for artifact_id in row.get("artifact_ids") or []:
                    artifact = self.store.get_workflow_artifact(str(artifact_id))
                    if artifact is not None:
                        artifacts.append({**artifact, "run_id": run_id})
            artifacts_by_id[run_id] = artifacts
        runtime_fingerprint = self.validator.resolve_resource_fingerprints(actor=actor, graph=graph)
        return WorkflowIncrementalPlanner().build(
            workflow={**workflow, "definition": graph.model_dump(mode="json")},
            current_revision=current_revision,
            task=resolved_task,
            mode=execution_mode,
            baseline_run=usable_runs,
            baseline_node_runs=node_runs_by_id,
            baseline_artifacts=artifacts_by_id,
            runtime_fingerprint=runtime_fingerprint,
        )

    @staticmethod
    def incremental_plan_payload(plan: IncrementalPlan) -> dict[str, Any]:
        return {
            "workflow_key": plan.workflow_key,
            "workflow_revision_no": plan.workflow_revision_no,
            "workflow_content_hash": plan.workflow_content_hash,
            "task_version": plan.task_version,
            "mode": plan.mode,
            "baseline_run_id": plan.baseline_run_id,
            "nodes": [
                {
                    "node_id": node.node_id,
                    "action": node.action,
                    "reason": node.reason,
                    "node_fingerprint": node.node_fingerprint,
                    "source_run_id": node.source_run_id,
                    "source_node_id": node.source_node_id,
                    "source_node_fingerprint": node.source_node_fingerprint,
                    "output_json": dict(node.output_json or {}),
                    "artifact_ids": list(node.artifact_ids),
                    "condition_results": [dict(item) for item in node.condition_results],
                }
                for node in plan.nodes
            ],
            "affected_node_ids": list(plan.affected_node_ids),
            "reusable_node_ids": list(plan.reusable_node_ids),
            "reasons": dict(plan.reasons),
            "warnings": list(plan.warnings),
        }

    @staticmethod
    def incremental_plan_preview_payload(plan: IncrementalPlan) -> dict[str, Any]:
        """Public, non-artifact-bearing execution-plan representation."""
        return {
            "mode": plan.mode,
            "baseline_run_id": plan.baseline_run_id,
            "affected_node_ids": list(plan.affected_node_ids),
            "reusable_node_ids": list(plan.reusable_node_ids),
            "nodes": [
                {
                    "node_id": node.node_id,
                    "action": node.action,
                    "reason": node.reason,
                    "source_run_id": node.source_run_id,
                    "source_node_id": node.source_node_id,
                    "node_fingerprint": node.node_fingerprint,
                }
                for node in plan.nodes
            ],
            "warnings": list(plan.warnings),
        }

    def workflow_run_start_payload(self, started: dict[str, Any]) -> dict[str, Any]:
        """Return the stable API response for a synchronously-created run."""
        run_id = str(started.get("run_id") or "")
        run = self.store.get_workflow_run(run_id) if run_id else None
        execution_mode = str((run or {}).get("execution_mode") or "normal")
        plan = self.incremental_plan_from_payload((run or {}).get("execution_plan") or {})
        return {
            "run_id": run_id,
            "run_status": str(started.get("status") or (run or {}).get("status") or "started"),
            "execution_mode": execution_mode,
            "plan": self.incremental_plan_preview_payload(plan),
        }

    @staticmethod
    def incremental_plan_from_payload(payload: dict[str, Any]) -> IncrementalPlan:
        return IncrementalPlan(
            workflow_key=str(payload.get("workflow_key") or ""),
            workflow_revision_no=payload.get("workflow_revision_no"),
            workflow_content_hash=payload.get("workflow_content_hash"),
            task_version=str(payload.get("task_version") or ""),
            mode=str(payload.get("mode") or "normal"),
            baseline_run_id=payload.get("baseline_run_id"),
            nodes=tuple(
                NodePlan(
                    node_id=str(node.get("node_id") or ""),
                    action=str(node.get("action") or "execute"),
                    reason=str(node.get("reason") or "plan_node_missing"),
                    node_fingerprint=str(node.get("node_fingerprint") or ""),
                    source_run_id=node.get("source_run_id"),
                    source_node_id=node.get("source_node_id"),
                    source_node_fingerprint=node.get("source_node_fingerprint"),
                    output_json=node.get("output_json") if isinstance(node.get("output_json"), dict) else None,
                    artifact_ids=tuple(str(item) for item in node.get("artifact_ids") or []),
                    condition_results=tuple(
                        item for item in node.get("condition_results") or [] if isinstance(item, dict)
                    ),
                )
                for node in payload.get("nodes") or []
                if isinstance(node, dict)
            ),
            affected_node_ids=tuple(str(item) for item in payload.get("affected_node_ids") or []),
            reusable_node_ids=tuple(str(item) for item in payload.get("reusable_node_ids") or []),
            reasons=dict(payload.get("reasons") or {}),
            warnings=tuple(str(item) for item in payload.get("warnings") or []),
        )

    @staticmethod
    def _definition_payload(workflow: dict[str, Any]) -> dict[str, Any]:
        payload = dict(workflow)
        payload.pop("definition_json", None)
        payload.pop("workflow_js", None)
        if "revision_no" not in payload:
            payload["revision_no"] = int(payload.get("current_revision_no") or 0)
        payload.pop("current_revision_no", None)
        return payload

    @staticmethod
    def _workflow_content_hash(
        graph_payload: dict[str, Any],
        name: str,
        description: str,
        profile_key: str,
        status: str,
        workflow_type: str,
    ) -> str:
        execution_definition = {
            "nodes": [
                {key: value for key, value in node.items() if key != "position"}
                for node in sorted(
                    graph_payload.get("nodes") or [],
                    key=lambda item: str(item.get("id") or ""),
                )
            ],
            "edges": sorted(
                graph_payload.get("edges") or [],
                key=lambda item: str(item.get("id") or ""),
            ),
        }
        fingerprint = json.dumps(
            {
                "definition": execution_definition,
                "name": name,
                "description": description,
                "profile_key": profile_key,
                "status": status,
                "workflow_type": workflow_type,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()

    @staticmethod
    def _workflow_revision_snapshot(workflow: dict[str, Any]) -> dict[str, Any]:
        """Capture the fields needed to reconstruct/diff a workflow version."""
        return {
            "workflow_key": workflow.get("workflow_key"),
            "name": workflow.get("name"),
            "description": workflow.get("description"),
            "profile_key": workflow.get("profile_key"),
            "status": workflow.get("status"),
            "workflow_type": workflow.get("workflow_type"),
            "definition": workflow.get("definition"),
        }

    # --- versioning & diff -----------------------------------------------

    def list_revisions(self, actor: str, workflow_key: str, *, limit: int = 100) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        if self.store.get_workflow_definition(workflow_key) is None:
            raise NotFound("workflow not found")
        revisions = self.store.workflows.list_definition_revisions(workflow_key, limit=limit)
        current = self.store.workflows.get_current_definition_revision_no(workflow_key)
        for rev in revisions:
            rev["is_current"] = rev.get("revision_no") == current
        return revisions

    def get_revision(self, actor: str, workflow_key: str, revision_no: int) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        if self.store.get_workflow_definition(workflow_key) is None:
            raise NotFound("workflow not found")
        revision = self.store.workflows.get_definition_revision(workflow_key, revision_no)
        if revision is None:
            raise NotFound("workflow revision not found")
        current = self.store.workflows.get_current_definition_revision_no(workflow_key)
        revision["is_current"] = revision.get("revision_no") == current
        return revision

    def diff_revisions(
        self, actor: str, workflow_key: str, *, from_no: int | None, to_no: int | None
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        if self.store.get_workflow_definition(workflow_key) is None:
            raise NotFound("workflow not found")
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
            "workflow_set_task 写入任务批次 workflow=%s run=%s received=%d action_total=%d created=%d updated=%d skipped_completed=%d skipped_running=%d skipped_historical=%d reopened_expired=%d",
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
        artifact_metadata = dict(metadata)
        if producer_node_id is not None:
            artifact_metadata.setdefault("producer_node_id", producer_node_id)
        if producer_node_fingerprint is not None:
            artifact_metadata.setdefault("producer_node_fingerprint", producer_node_fingerprint)
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
            metadata=artifact_metadata,
            producer_node_id=producer_node_id,
            producer_node_fingerprint=producer_node_fingerprint,
        )
        if producer_node_id is not None:
            self.store.associate_workflow_run_artifacts(
                run_id,
                producer_node_id,
                [saved["artifact_id"]],
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

        registry = getattr(self.agent_service, "control_registry", None)
        if registry is not None and registry.is_workflow_stop_requested(run_id):
            return {"status": "stopped"}
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
