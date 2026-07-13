from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError as PydanticValidationError

from agent_bridge.automation.workflows.definition import WorkflowGraph
from agent_bridge.automation.workflows.models import WorkflowType
from agent_bridge.automation.workflows.references import parse_reference
from agent_bridge.automation.workflows.validation import (
    WorkflowDefinitionValidationError,
    WorkflowValidationIssue,
    WorkflowValidationResult,
    collect_graph_issues,
)
from agent_bridge.core.domain import NotFound
from agent_bridge.storage.sqlite import SQLiteStore


class _WorkflowValidationRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    workflow_key: str
    name: str
    description: str = ""
    profile_key: str
    definition: WorkflowGraph
    status: str = "active"
    workflow_type: WorkflowType = WorkflowType.operation


@dataclass(frozen=True)
class _NormalizedWorkflow:
    profile_key: str
    workflow_type: WorkflowType
    definition: WorkflowGraph


class WorkflowValidator:
    def __init__(
        self,
        *,
        store: SQLiteStore,
        agent_service: Any = None,
        skills: Any = None,
        scripts: Any = None,
    ) -> None:
        self.store = store
        self.agent_service = agent_service
        self.skills = skills
        self.scripts = scripts

    def validate(self, *, actor: str, workflow: dict[str, Any]) -> WorkflowValidationResult:
        _normalized, result = self._validate(actor=actor, workflow=workflow)
        return result

    def require_valid(self, *, actor: str, workflow: dict[str, Any]) -> WorkflowGraph:
        normalized, result = self._validate(actor=actor, workflow=workflow)
        if result.errors:
            raise WorkflowDefinitionValidationError(result.errors)
        if normalized is None:
            raise WorkflowDefinitionValidationError(result.errors)
        return normalized.definition

    def _validate(
        self,
        *,
        actor: str,
        workflow: dict[str, Any],
    ) -> tuple[_NormalizedWorkflow | None, WorkflowValidationResult]:
        try:
            parsed = _WorkflowValidationRequest.model_validate(workflow)
        except PydanticValidationError as exc:
            issues = self._definition_issues(workflow, exc)
            return None, WorkflowValidationResult(valid=False, errors=issues, warnings=[])

        issues = collect_graph_issues(parsed.definition, parsed.workflow_type)
        if self.store.get_project_profile(parsed.profile_key) is None:
            issues.append(
                WorkflowValidationIssue(
                    scope="workflow",
                    id=None,
                    field="profile_key",
                    code="missing_profile",
                    message="Profile 不存在",
                )
            )
        issues.extend(self._resource_issues(actor=actor, graph=parsed.definition))
        result = WorkflowValidationResult(valid=not issues, errors=issues, warnings=[])
        normalized = _NormalizedWorkflow(
            profile_key=parsed.profile_key,
            workflow_type=parsed.workflow_type,
            definition=parsed.definition,
        )
        return normalized, result

    def _definition_issues(
        self,
        workflow: dict[str, Any],
        error: PydanticValidationError,
    ) -> list[WorkflowValidationIssue]:
        issues: list[WorkflowValidationIssue] = []
        for detail in error.errors():
            scope, identifier, field = self._issue_location(workflow, detail.get("loc") or ())
            error_type = str(detail.get("type") or "")
            code, message = self._pydantic_code_and_message(error_type)
            issues.append(
                WorkflowValidationIssue(
                    scope=scope,
                    id=identifier,
                    field=field,
                    code=code,
                    message=message,
                )
            )
        return issues

    @staticmethod
    def _issue_location(
        workflow: dict[str, Any],
        location: tuple[Any, ...] | list[Any],
    ) -> tuple[str, str | None, str | None]:
        parts = list(location)
        if len(parts) >= 3 and parts[0] == "definition" and parts[1] in {"nodes", "edges"} and isinstance(parts[2], int):
            collection = str(parts[1])
            index = parts[2]
            field_parts = [str(part) for part in parts[3:] if part != "config"]
            identifier = None
            definition = workflow.get("definition")
            if isinstance(definition, dict):
                items = definition.get(collection)
                if isinstance(items, list) and 0 <= index < len(items) and isinstance(items[index], dict):
                    identifier = str(items[index].get("id")) if items[index].get("id") is not None else None
            scope = "node" if collection == "nodes" else "edge"
            field = ".".join(field_parts) or None
            return scope, identifier, field

        field = ".".join(str(part) for part in parts if part != "definition") or None
        return "workflow", None, field

    @staticmethod
    def _pydantic_code_and_message(error_type: str) -> tuple[str, str]:
        if error_type == "missing":
            return "missing_field", "缺少必填字段"
        if error_type == "extra_forbidden":
            return "unknown_field", "包含未支持的字段"
        if error_type in {"enum", "literal_error"}:
            return "invalid_value", "字段值不合法"
        if error_type.endswith("_type") or error_type == "model_attributes_type":
            return "invalid_type", "字段类型不合法"
        return "invalid_definition", "工作流定义格式不合法"

    def _resource_issues(self, *, actor: str, graph: WorkflowGraph) -> list[WorkflowValidationIssue]:
        issues: list[WorkflowValidationIssue] = []
        input_types: dict[str, tuple[str, str]] = {}
        for node in graph.nodes:
            config = node.config
            if node.type in {"agent", "output"}:
                try:
                    backend_missing = self.agent_service is None or self.agent_service.coding_agents.get(config.backend_key) is None
                except Exception:
                    backend_missing = True
                if backend_missing:
                    issues.append(
                        WorkflowValidationIssue(
                            scope="node",
                            id=node.id,
                            field="config.backend_key",
                            code="unknown_backend",
                            message=f"未知后端: {config.backend_key}",
                        )
                    )
                for skill_name in config.skill_names:
                    try:
                        if self.skills is None:
                            raise NotFound("skill service unavailable")
                        self.skills.get_skill(actor, skill_name)
                    except Exception:
                        issues.append(
                            WorkflowValidationIssue(
                                scope="node",
                                id=node.id,
                                field="config.skill_names",
                                code="missing_skill",
                                message=f"技能不存在: {skill_name}",
                            )
                        )
            if node.type == "script":
                script = self.store.scripts.get_script(config.script_key)
                if script is None or script.get("status") != "active":
                    issues.append(
                        WorkflowValidationIssue(
                            scope="node",
                            id=node.id,
                            field="config.script_key",
                            code="missing_script",
                            message=f"脚本不存在或未启用: {config.script_key}",
                        )
                    )
                    continue
                schema = script.get("input_schema") or {}
                required = schema.get("required") or []
                properties = schema.get("properties") or {}
                for field in required:
                    if field not in config.params:
                        issues.append(
                            WorkflowValidationIssue(
                                scope="node",
                                id=node.id,
                                field=f"config.params.{field}",
                                code="missing_script_param",
                                message="缺少脚本必填参数",
                            )
                        )
                for field, value in config.params.items():
                    reference = parse_reference(value)
                    if reference is None or not reference.startswith("input."):
                        continue
                    path = reference.removeprefix("input.")
                    field_type = str((properties.get(field) or {}).get("type") or "")
                    previous = input_types.get(path)
                    if previous and previous[0] and field_type and previous[0] != field_type:
                        issues.append(
                            WorkflowValidationIssue(
                                scope="node",
                                id=node.id,
                                field=f"config.params.{field}",
                                code="input_type_conflict",
                                message=f"手动输入类型冲突: input.{path}",
                            )
                        )
                    elif field_type:
                        input_types[path] = (field_type, node.id)
            if node.type == "output" and (config.path.startswith("/") or ".." in config.path.split("/")):
                issues.append(
                    WorkflowValidationIssue(
                        scope="node",
                        id=node.id,
                        field="config.path",
                        code="invalid_path",
                        message="输出路径不能为绝对路径或包含 ..",
                    )
                )
        return issues
