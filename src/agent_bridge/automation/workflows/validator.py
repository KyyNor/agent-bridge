from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, ConfigDict, ValidationError as PydanticValidationError

from agent_bridge.automation.workflows.definition import WorkflowGraph, WorkflowNode, execution_fingerprint
from agent_bridge.automation.workflows.models import WorkflowStatus, WorkflowType
from agent_bridge.automation.workflows.references import REFERENCE_RE, parse_reference
from agent_bridge.automation.workflows.validation import (
    WorkflowDefinitionValidationError,
    WorkflowValidationIssue,
    WorkflowValidationResult,
    collect_graph_issues,
)
from agent_bridge.core.domain import NotFound
from agent_bridge.storage.sqlite import SQLiteStore


def _json_type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


class _WorkflowValidationRequest(BaseModel):
    # Persisted workflow rows contain storage metadata. Validation requires the
    # complete public workflow contract while ignoring those database fields.
    model_config = ConfigDict(extra="ignore")

    workflow_key: str
    name: str
    description: str
    profile_key: str
    definition: WorkflowGraph
    status: WorkflowStatus
    workflow_type: WorkflowType


@dataclass(frozen=True)
class _NormalizedWorkflow:
    profile_key: str
    status: WorkflowStatus
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

    def resolve_resource_fingerprints(self, *, actor: str, graph: WorkflowGraph) -> dict[str, str | None]:
        """Resolve stable execution-resource fingerprints for incremental plans.

        ``None`` deliberately means that the backing resource is live or its
        version cannot be established.  A planner must then execute the node
        rather than reuse a result produced against an unknown resource.
        """
        return {
            node.id: self.resolve_resource_fingerprint(actor=actor, node=node)
            for node in graph.nodes
        }

    def resolve_resource_fingerprint(self, *, actor: str, node: WorkflowNode) -> str | None:
        config = node.config
        if node.type == "get_task":
            return execution_fingerprint({"resource": "workflow-task-input"})
        if node.type == "script":
            script = self._resolve_script(actor=actor, script_key=config.script_key)
            if not isinstance(script, dict):
                return None
            version = script.get("content_hash") or script.get("revision_no") or script.get("updated_at")
            if version is None and script.get("code") is not None:
                version = execution_fingerprint({"code": script["code"]})
            return (
                execution_fingerprint({"script_key": config.script_key, "version": version})
                if version is not None
                else None
            )
        if node.type not in {"agent", "output"}:
            return None
        if bool(getattr(config, "mcp_enabled", False)):
            # External MCP data is live unless the caller supplies a dedicated,
            # versioned runtime fingerprint to the planner.
            return None
        backend = None
        try:
            if self.agent_service is not None:
                backend = self.agent_service.coding_agents.get(config.backend_key)
        except Exception:
            backend = None
        if backend is None:
            return None
        version = next(
            (
                getattr(backend, field, None)
                for field in ("version", "model_version", "model", "model_name")
                if getattr(backend, field, None) is not None
            ),
            None,
        )
        if version is None:
            return None
        return execution_fingerprint({"backend_key": config.backend_key, "version": version})

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
        profile = self.store.get_project_profile(parsed.profile_key)
        if profile is None:
            issues.append(
                WorkflowValidationIssue(
                    scope="workflow",
                    id=None,
                    field="profile_key",
                    code="missing_profile",
                    message=f"Profile 不存在：{parsed.profile_key}",
                )
            )
        elif profile.get("status") != "active":
            issues.append(
                WorkflowValidationIssue(
                    scope="workflow",
                    id=None,
                    field="profile_key",
                    code="inactive_profile",
                    message=f"Profile 未启用：{parsed.profile_key}",
                )
            )
        issues.extend(self._resource_issues(actor=actor, graph=parsed.definition))
        issues.extend(self._reference_issues(actor=actor, graph=parsed.definition))
        issues = self._dedupe_issues(issues)
        result = WorkflowValidationResult(valid=not issues, errors=issues, warnings=[])
        normalized = _NormalizedWorkflow(
            profile_key=parsed.profile_key,
            status=parsed.status,
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
            field_parts = list(parts[3:])
            if collection == "nodes" and field_parts and field_parts[0] in {
                "get_task",
                "agent",
                "script",
                "output",
            }:
                field_parts.pop(0)
            field_parts = [str(part) for part in field_parts if part != "config"]
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
                if node.type == "agent" and config.result_mode == "json" and config.output_schema:
                    try:
                        Draft202012Validator.check_schema(config.output_schema)
                    except SchemaError:
                        issues.append(
                            WorkflowValidationIssue(
                                scope="node",
                                id=node.id,
                                field="config.output_schema",
                                code="invalid_output_schema",
                                message="JSON 输出 Schema 不合法",
                            )
                        )
                backend = None
                try:
                    if self.agent_service is not None:
                        backend = self.agent_service.coding_agents.get(config.backend_key)
                except Exception:
                    backend = None
                if backend is None:
                    issues.append(
                        WorkflowValidationIssue(
                            scope="node",
                            id=node.id,
                            field="config.backend_key",
                            code="unknown_backend",
                            message=f"未知后端: {config.backend_key}",
                        )
                    )
                elif config.mcp_enabled and not bool(
                    getattr(getattr(backend, "capabilities", None), "supports_mcp", False)
                ):
                    issues.append(
                        WorkflowValidationIssue(
                            scope="node",
                            id=node.id,
                            field="config.mcp_enabled",
                            code="unsupported_mcp",
                            message=f"后端不支持 MCP: {config.backend_key}",
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
                script = self._resolve_script(actor=actor, script_key=config.script_key)
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
                issues.extend(
                    self._script_param_schema_issues(
                        node_id=node.id,
                        schema=schema,
                        params=config.params,
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

    def _resolve_script(self, *, actor: str, script_key: str) -> dict[str, Any] | None:
        try:
            if self.scripts is None:
                return None
            return self.scripts.get_script(actor, script_key)
        except Exception:
            return None

    @staticmethod
    def _script_param_schema_issues(
        *,
        node_id: str,
        schema: dict[str, Any],
        params: dict[str, Any],
    ) -> list[WorkflowValidationIssue]:
        issues: list[WorkflowValidationIssue] = []
        errors = sorted(
            Draft202012Validator(schema).iter_errors(params),
            key=lambda item: (list(item.absolute_path), str(item.validator)),
        )
        for error in errors:
            path = [str(part) for part in error.absolute_path]
            if error.validator == "required" and isinstance(error.instance, dict):
                missing = next(
                    (
                        str(field)
                        for field in error.validator_value
                        if isinstance(field, str) and field not in error.instance
                    ),
                    None,
                )
                if missing:
                    field = ".".join(["config", "params", *path, missing])
                    issues.append(
                        WorkflowValidationIssue(
                            scope="node",
                            id=node_id,
                            field=field,
                            code="missing_script_param",
                            message=f"缺少脚本必填参数：{missing}",
                        )
                    )
                continue
            if error.validator == "additionalProperties":
                instance = error.instance if isinstance(error.instance, dict) else {}
                error_schema = error.schema if isinstance(error.schema, dict) else {}
                allowed = error_schema.get("properties")
                allowed_names = set(allowed) if isinstance(allowed, dict) else set()
                unexpected = sorted(str(key) for key in instance if str(key) not in allowed_names)
                prefix = ".".join(path)
                unexpected_names = [f"{prefix}.{name}" if prefix else name for name in unexpected]
                supported = ", ".join(sorted(str(name) for name in allowed_names)) or "无"
                issues.append(
                    WorkflowValidationIssue(
                        scope="node",
                        id=node_id,
                        field=".".join(["config", "params", *path]),
                        code="invalid_script_param",
                        message=f"脚本不接受参数：{', '.join(unexpected_names) or '未知参数'}；支持的参数：{supported}",
                    )
                )
                continue
            if error.validator == "type":
                if parse_reference(error.instance) is not None:
                    continue
                expected = error.validator_value
                expected_text = "/".join(expected) if isinstance(expected, list) else str(expected)
                issues.append(
                    WorkflowValidationIssue(
                        scope="node",
                        id=node_id,
                        field=".".join(["config", "params", *path]),
                        code="invalid_script_param_type",
                        message=f"脚本参数类型不匹配，期望 {expected_text}，实际是 {_json_type_name(error.instance)}",
                    )
                )
                continue
            if parse_reference(error.instance) is not None:
                continue
            issues.append(
                WorkflowValidationIssue(
                    scope="node",
                    id=node_id,
                    field=".".join(["config", "params", *path]),
                    code="invalid_script_param",
                    message=f"脚本参数不符合输入 Schema：{error.message}",
                )
            )
        return issues

    def _reference_issues(self, *, actor: str, graph: WorkflowGraph) -> list[WorkflowValidationIssue]:
        issues: list[WorkflowValidationIssue] = []
        incoming = {node.id: [] for node in graph.nodes}
        nodes = {node.id: node for node in graph.nodes}
        for edge in graph.edges:
            if edge.source in nodes and edge.target in incoming and edge.source != edge.target:
                incoming[edge.target].append(edge.source)

        for node in graph.nodes:
            ancestors = self._ancestors(node.id, incoming)
            for field, value in self._template_values(node):
                for match in REFERENCE_RE.finditer(value):
                    path = match.group(1)
                    namespace = path.split(".", 1)[0]
                    if namespace not in {"input", "task", "nodes"}:
                        issues.append(
                            WorkflowValidationIssue(
                                scope="node",
                                id=node.id,
                                field=field,
                                code="invalid_reference_namespace",
                                message="引用命名空间只允许 input、task、nodes",
                            )
                        )
                        continue
                    if namespace == "input":
                        if "." not in path:
                            issues.append(self._missing_reference_path("node", node.id, field, path))
                        continue
                    if namespace == "task":
                        if not self._valid_task_path(path):
                            issues.append(self._missing_reference_path("node", node.id, field, path))
                        continue
                    referenced_id = self._referenced_node_id(path)
                    if referenced_id is None:
                        issues.append(self._missing_reference_path("node", node.id, field, path))
                        continue
                    if referenced_id not in ancestors:
                        issues.append(
                            WorkflowValidationIssue(
                                scope="node",
                                id=node.id,
                                field=field,
                                code="invalid_reference",
                                message=f"节点引用必须来自祖先节点: {referenced_id}",
                            )
                        )
                        continue
                    if not self._node_output_path_exists(
                        actor=actor,
                        node=nodes[referenced_id],
                        path=path,
                    ):
                        issues.append(self._missing_reference_path("node", node.id, field, path))

        for edge in graph.edges:
            if edge.condition is None:
                continue
            path = edge.condition.field
            parts = path.split(".")
            if len(parts) < 3 or parts[0] != "nodes":
                issues.append(
                    WorkflowValidationIssue(
                        scope="edge",
                        id=edge.id,
                        field="condition.field",
                        code="invalid_reference_namespace",
                        message="条件字段只能引用来源节点或其祖先节点输出",
                    )
                )
                continue
            referenced_id = parts[1]
            guaranteed = self._ancestors(edge.source, incoming) | {edge.source}
            if referenced_id not in guaranteed:
                issues.append(
                    WorkflowValidationIssue(
                        scope="edge",
                        id=edge.id,
                        field="condition.field",
                        code="invalid_reference",
                        message=f"条件字段必须引用来源节点或其祖先节点: {referenced_id}",
                    )
                )
                continue
            if referenced_id not in nodes or not self._node_output_path_exists(
                actor=actor,
                node=nodes[referenced_id],
                path=path,
            ):
                issues.append(self._missing_reference_path("edge", edge.id, "condition.field", path))
        return issues

    @staticmethod
    def _template_values(node: WorkflowNode) -> Iterable[tuple[str, str]]:
        if node.type == "agent":
            yield "config.prompt", node.config.prompt
            return
        if node.type == "output":
            yield "config.prompt", node.config.prompt
            yield "config.path", node.config.path
            yield "config.title", node.config.title
            return
        if node.type != "script":
            return

        def visit(value: Any, path: list[str]) -> Iterable[tuple[str, str]]:
            if isinstance(value, str):
                yield ".".join(["config", "params", *path]), value
            elif isinstance(value, dict):
                for key, item in value.items():
                    yield from visit(item, [*path, str(key)])
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    yield from visit(item, [*path, str(index)])

        yield from visit(node.config.params, [])

    @staticmethod
    def _ancestors(node_id: str, incoming: dict[str, list[str]]) -> set[str]:
        seen: set[str] = set()
        pending = list(incoming.get(node_id, []))
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            pending.extend(incoming.get(current, []))
        return seen

    @staticmethod
    def _valid_task_path(path: str) -> bool:
        parts = path.split(".")
        if len(parts) < 2:
            return False
        if parts[1] == "payload":
            return True
        return len(parts) == 2 and parts[1] in {"task_key", "task_version", "type"}

    @staticmethod
    def _referenced_node_id(path: str) -> str | None:
        parts = path.split(".")
        if len(parts) < 3 or parts[0] != "nodes" or not parts[1]:
            return None
        return parts[1]

    def _node_output_path_exists(self, *, actor: str, node: WorkflowNode, path: str) -> bool:
        parts = path.split(".")
        if len(parts) < 3 or parts[:1] != ["nodes"] or parts[1] != node.id or parts[2] != "output":
            return False
        if len(parts) == 3:
            return True
        schema = self._node_output_schema(actor=actor, node=node)
        if schema is None:
            return True
        return self._schema_path_exists(schema, parts[3:])

    def _node_output_schema(self, *, actor: str, node: WorkflowNode) -> dict[str, Any] | None:
        if node.type == "agent":
            if node.config.result_mode == "json":
                return node.config.output_schema
            return {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "additionalProperties": False,
            }
        if node.type == "script":
            script = self._resolve_script(actor=actor, script_key=node.config.script_key)
            schema = script.get("output_schema") if script else None
            return schema if isinstance(schema, dict) else None
        if node.type == "output":
            return {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "content": {"type": "string"},
                    "artifact_ids": {"type": "array"},
                },
                "additionalProperties": False,
            }
        return None

    @staticmethod
    def _schema_path_exists(schema: dict[str, Any], path: list[str]) -> bool:
        current: Any = schema
        for part in path:
            if not isinstance(current, dict):
                return False
            properties = current.get("properties")
            if isinstance(properties, dict) and part in properties:
                current = properties[part]
                continue
            additional = current.get("additionalProperties")
            if additional is True:
                return True
            if isinstance(additional, dict):
                current = additional
                continue
            return False
        return True

    @staticmethod
    def _missing_reference_path(
        scope: str,
        identifier: str | None,
        field: str,
        path: str,
    ) -> WorkflowValidationIssue:
        return WorkflowValidationIssue(
            scope=scope,
            id=identifier,
            field=field,
            code="invalid_reference_path",
            message=f"引用字段不存在: {path}",
        )

    @staticmethod
    def _dedupe_issues(issues: list[WorkflowValidationIssue]) -> list[WorkflowValidationIssue]:
        seen: set[tuple[Any, ...]] = set()
        result: list[WorkflowValidationIssue] = []
        for issue in issues:
            key = (issue.scope, issue.id, issue.field, issue.code, issue.message)
            if key in seen:
                continue
            seen.add(key)
            result.append(issue)
        return result
