"""工作流定义导入/导出领域服务。

从 ``WorkflowService`` 抽出的定义包导入、确认与导出：解析 envelope、生成
diff、落库预览会话、确认时回写定义。门面通过薄转发维持对外签名兼容。
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Callable

from pydantic import ValidationError as PydanticValidationError

from agent_bridge.core.domain import (
    AccessDenied,
    ConflictError,
    NotFound,
    ValidationError,
    require_admin_user,
)
from agent_bridge.core.diff import text_diff, workflow_structured_diff
from agent_bridge.core.timeutil import parse_utc, utc_iso, utc_now
from agent_bridge.automation.workflows.definition import (
    WorkflowGraph,
    normalize_summary_graph,
)
from agent_bridge.automation.workflows.models import WorkflowStatus, WorkflowType
from agent_bridge.automation.workflows.revisions import (
    definition_payload,
    workflow_content_hash,
    workflow_revision_snapshot,
)

if TYPE_CHECKING:
    from agent_bridge.storage.sqlite import SQLiteStore

logger = logging.getLogger(__name__)

WORKFLOW_IMPORT_MODES = frozenset({"auto", "new", "overwrite"})


class DefinitionImportService:
    """工作流定义的导出、导入预览与确认。"""

    def __init__(
        self,
        *,
        store: "SQLiteStore",
        admins: set[str],
        validator: Any,
        upsert_definition: Callable[..., dict[str, Any]],
    ) -> None:
        self.store = store
        self.admins = admins
        self.validator = validator
        self.upsert_definition = upsert_definition

    def export_definition(self, actor: str, workflow_key: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        with self.store.transaction():
            workflow = self.store.get_workflow_definition(workflow_key)
            if workflow is None:
                raise NotFound("workflow not found")
            public = definition_payload(workflow)
            if not isinstance(public.get("definition"), dict):
                raise NotFound("workflow has no exportable definition")
            current_revision_no = int(workflow.get("current_revision_no") or 0)
            revision = (
                self.store.workflows.get_definition_revision(workflow_key, current_revision_no)
                if current_revision_no > 0
                else None
            )
            if revision is None:
                content_hash = workflow_content_hash(
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
                        snapshot=workflow_revision_snapshot(public),
                        actor=actor,
                        source="edit",
                    )
            if revision is None:
                raise NotFound("workflow revision not found")
            return {
                "format": "agent-bridge.workflow",
                "format_version": 1,
                "exported_at": utc_iso(),
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
                else workflow_revision_snapshot(definition_payload(existing))
            )
            after = workflow_revision_snapshot(imported)
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
        expires_at = utc_now() + timedelta(minutes=15)
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
            "expires_at": utc_iso(expires_at),
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
        now = utc_now()
        with self.store.transaction():
            snapshot = self.store.workflows.get_workflow_definition_import(import_id)
            if snapshot is None:
                raise NotFound("workflow definition import not found")
            if snapshot.get("actor") != actor:
                raise AccessDenied("workflow definition import belongs to another actor")
            if snapshot.get("status") != "previewed":
                raise ConflictError("导入预览已失效，请重新选择文件并预览")
            try:
                expires_at = parse_utc(snapshot["expires_at"])
                if expires_at is None:
                    raise ValidationError("导入预览过期时间格式无效")
            except (KeyError, TypeError, ValueError) as exc:
                raise ValidationError("workflow definition import expiry is invalid") from exc
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
