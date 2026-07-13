from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

from agent_bridge.automation.workflows.definition import WorkflowGraph
from agent_bridge.automation.workflows.models import WorkflowType
from agent_bridge.core.domain import ValidationError


@dataclass(frozen=True)
class WorkflowValidationIssue:
    scope: Literal["workflow", "node", "edge"]
    id: str | None
    field: str | None
    code: str
    message: str


@dataclass(frozen=True)
class WorkflowValidationResult:
    valid: bool
    errors: list[WorkflowValidationIssue]
    warnings: list[WorkflowValidationIssue]


class WorkflowDefinitionValidationError(ValidationError):
    def __init__(self, issues: list[WorkflowValidationIssue]) -> None:
        super().__init__("工作流定义校验失败")
        self.issues = issues


def validate_graph(graph: WorkflowGraph, workflow_type: WorkflowType) -> None:
    issues = collect_graph_issues(graph, workflow_type)
    if issues:
        raise WorkflowDefinitionValidationError(issues)


def collect_graph_issues(graph: WorkflowGraph, workflow_type: WorkflowType) -> list[WorkflowValidationIssue]:
    issues: list[WorkflowValidationIssue] = []
    node_ids: set[str] = set()
    edge_ids: set[str] = set()
    for node in graph.nodes:
        if node.id in node_ids:
            issues.append(_issue("node", node.id, "id", "duplicate_id", f"节点 ID 重复: {node.id}"))
        node_ids.add(node.id)
        if node.type == "agent" and node.config.result_mode == "json" and not node.config.output_schema:
            issues.append(
                _issue("node", node.id, "config.output_schema", "missing_output_schema", "JSON 输出必须提供 schema")
            )
    for edge in graph.edges:
        if edge.id in edge_ids:
            issues.append(_issue("edge", edge.id, "id", "duplicate_id", f"边 ID 重复: {edge.id}"))
        edge_ids.add(edge.id)
        if edge.source not in node_ids:
            issues.append(_issue("edge", edge.id, "source", "missing_node", f"来源节点不存在: {edge.source}"))
        if edge.target not in node_ids:
            issues.append(_issue("edge", edge.id, "target", "missing_node", f"目标节点不存在: {edge.target}"))
        if edge.source == edge.target:
            issues.append(_issue("edge", edge.id, "target", "self_reference", "边不能连接节点自身"))

    incoming = {node.id: [] for node in graph.nodes}
    outgoing = {node.id: [] for node in graph.nodes}
    for edge in graph.edges:
        if edge.source in outgoing and edge.target in incoming and edge.source != edge.target:
            outgoing[edge.source].append(edge.target)
            incoming[edge.target].append(edge.source)
    if _contains_cycle(outgoing):
        issues.append(_issue("workflow", None, None, "cycle_detected", "工作流不能包含环"))

    task_nodes = [node for node in graph.nodes if node.type == "get_task"]
    if len(task_nodes) > 1:
        issues.append(_issue("workflow", None, None, "too_many_task_roots", "工作流最多只能包含一个获取任务节点"))
    if task_nodes and incoming[task_nodes[0].id]:
        issues.append(_issue("node", task_nodes[0].id, None, "invalid_root", "获取任务节点必须是无入边起点"))
    roots = [node.id for node in graph.nodes if not incoming[node.id]]
    if task_nodes and roots != [task_nodes[0].id]:
        issues.append(_issue("node", task_nodes[0].id, None, "invalid_root", "获取任务节点必须是工作流唯一根节点"))

    for edge in graph.edges:
        condition = edge.condition
        if condition is None or not condition.field.startswith("nodes."):
            continue
        referenced_id = condition.field.split(".", 2)[1]
        guaranteed = _ancestors(edge.source, incoming) | {edge.source}
        if referenced_id not in guaranteed:
            issues.append(
                _issue(
                    "edge",
                    edge.id,
                    "condition.field",
                    "invalid_reference",
                    f"条件字段必须引用来源节点或其祖先节点: {referenced_id}",
                )
            )

    for node in graph.nodes:
        ancestors = _ancestors(node.id, incoming)
        for field, template in _node_templates(node):
            for referenced in re.findall(r"\{\{\s*nodes\.([A-Za-z0-9_.-]+)", template):
                referenced_id = referenced.split(".", 1)[0]
                if referenced_id not in ancestors:
                    issues.append(_issue("node", node.id, field, "invalid_reference", f"节点引用必须来自祖先节点: {referenced_id}"))

    if workflow_type is WorkflowType.summary:
        _validate_summary_graph(graph, incoming, outgoing, issues)
    return issues


def _validate_summary_graph(graph, incoming, outgoing, issues) -> None:
    outputs = [node for node in graph.nodes if node.type == "output"]
    if len(outputs) != 2:
        issues.append(
            _issue("workflow", None, None, "invalid_summary_outputs", "总结型工作流必须且只能包含 Markdown 和 HTML 输出节点")
        )
        return
    markdown = next((node for node in outputs if node.config.format == "markdown"), None)
    html = next((node for node in outputs if node.config.format == "html"), None)
    if markdown is None or html is None:
        issues.append(
            _issue("workflow", None, None, "invalid_summary_outputs", "总结型工作流必须包含一组 Markdown 和 HTML 输出节点")
        )
        return
    if [node.id for node in graph.nodes[-2:]] != [markdown.id, html.id]:
        issues.append(
            _issue("workflow", None, None, "invalid_summary_order", "总结输出节点必须按 Markdown、HTML 顺序位于图末端")
        )
    connecting_edges = [
        edge for edge in graph.edges if edge.source == markdown.id and edge.target == html.id
    ]
    if not connecting_edges:
        issues.append(_issue("workflow", None, None, "missing_summary_edge", "Markdown 输出必须连接到 HTML 输出"))
    elif len(connecting_edges) != 1 or connecting_edges[0].condition is not None:
        issues.append(
            _issue("edge", connecting_edges[0].id, "condition", "invalid_summary_edge", "Markdown 到 HTML 必须使用唯一无条件边")
        )
    if incoming.get(html.id) != [markdown.id]:
        issues.append(_issue("node", html.id, None, "invalid_summary_dependency", "HTML 输出只能直接依赖 Markdown 主报告"))
    if outgoing.get(html.id):
        issues.append(_issue("node", html.id, None, "invalid_terminal_node", "HTML 输出必须是末端节点"))


def _contains_cycle(outgoing: dict[str, list[str]]) -> bool:
    active: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> bool:
        if node_id in active:
            return True
        if node_id in visited:
            return False
        active.add(node_id)
        if any(visit(target) for target in outgoing[node_id]):
            return True
        active.remove(node_id)
        visited.add(node_id)
        return False

    return any(visit(node_id) for node_id in outgoing)


def _ancestors(node_id: str, incoming: dict[str, list[str]]) -> set[str]:
    seen: set[str] = set()
    pending = list(incoming[node_id])
    while pending:
        parent = pending.pop()
        if parent not in seen:
            seen.add(parent)
            pending.extend(incoming[parent])
    return seen


def _node_templates(node) -> list[tuple[str, str]]:
    if node.type == "agent":
        return [("config.prompt", node.config.prompt)]
    if node.type == "output":
        return [("config.prompt", node.config.prompt), ("config.path", node.config.path)]
    if node.type == "script":
        return [(f"config.params.{key}", value) for key, value in node.config.params.items() if isinstance(value, str)]
    return []


def _issue(
    scope: Literal["workflow", "node", "edge"],
    identifier: str | None,
    field: str | None,
    code: str,
    message: str,
) -> WorkflowValidationIssue:
    return WorkflowValidationIssue(scope=scope, id=identifier, field=field, code=code, message=message)
