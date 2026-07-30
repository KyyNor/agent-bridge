from __future__ import annotations

import json
from hashlib import sha256
from typing import Annotated, Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field

from agent_bridge.core.domain import ValidationError
from agent_bridge.automation.workflows.models import WorkflowType


class NodePosition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float
    y: float


class EdgeCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    operator: Literal["equals", "not_equals", "exists", "not_exists", "contains"]
    value: Any | None = None


class WorkflowEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source: str
    target: str
    condition: EdgeCondition | None = None
    system_role: Literal["summary_markdown_to_html"] | None = None


class GetTaskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    on_empty: Literal["terminate", "continue"] = "terminate"


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str
    backend_key: str
    mcp_enabled: bool = True
    skill_names: list[str] = Field(default_factory=list)
    # 运行控制参数不参与增量复用判定；仅改变等待 Agent 的最长时间。
    timeout_seconds: int = Field(default=600, ge=1, le=86_400)
    result_mode: Literal["text", "json"] = "text"
    output_schema: dict[str, Any] | None = None


class ScriptConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    script_key: str
    params: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=60, ge=1, le=600)


class OutputConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["markdown", "html"]
    title: str
    path: str
    tags: list[str] = Field(default_factory=list)
    prompt: str
    backend_key: str
    mcp_enabled: bool = False
    skill_names: list[str] = Field(default_factory=list)
    # 输出节点同样通过 Coding Agent 生成内容，需要独立的运行时上限。
    timeout_seconds: int = Field(default=600, ge=1, le=86_400)
    system_role: Literal["summary_markdown", "summary_html"] | None = None


class BaseNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    position: NodePosition


class GetTaskNode(BaseNode):
    type: Literal["get_task"]
    config: GetTaskConfig = Field(default_factory=GetTaskConfig)


class AgentNode(BaseNode):
    type: Literal["agent"]
    config: AgentConfig


class ScriptNode(BaseNode):
    type: Literal["script"]
    config: ScriptConfig


class OutputNode(BaseNode):
    type: Literal["output"]
    config: OutputConfig


WorkflowNode = Annotated[
    GetTaskNode | AgentNode | ScriptNode | OutputNode,
    Field(discriminator="type"),
]


class WorkflowGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[WorkflowNode] = Field(default_factory=list)
    edges: list[WorkflowEdge] = Field(default_factory=list)


_PRESENTATION_KEYS = frozenset({"position", "metadata", "display", "ui"})


def normalize_execution_value(value: Any) -> Any:
    """Return a JSON-safe value with graph presentation-only data removed.

    The representation deliberately preserves list order: workflow configuration
    lists can be semantically ordered, while mappings are made stable by
    ``stable_json_dumps`` below.
    """
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {
            str(key): normalize_execution_value(item)
            for key, item in value.items()
            if str(key) not in _PRESENTATION_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [normalize_execution_value(item) for item in value]
    return value


def stable_json_dumps(value: Any) -> str:
    """Serialize execution configuration deterministically for fingerprints."""
    return json.dumps(
        normalize_execution_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def execution_fingerprint(value: Any) -> str:
    """Return the SHA-256 fingerprint of a normalized execution value."""
    return sha256(stable_json_dumps(value).encode("utf-8")).hexdigest()


def node_execution_payload(node: WorkflowNode | Mapping[str, Any], *, resource_fingerprint: Any = None) -> dict[str, Any]:
    """Pick node fields that affect produced output, excluding run controls."""
    raw = node.model_dump(mode="json") if isinstance(node, BaseModel) else dict(node)
    config = dict(raw.get("config") or {})
    # 超时只控制本次等待时长，不会改变相同输入下的处理语义或产物。
    # 排除它可确保仅调整超时不会让增量执行失去既有结果复用资格。
    if raw.get("type") in {"agent", "output"}:
        config.pop("timeout_seconds", None)
    if raw.get("type") == "output":
        # Output 标题仅用于编辑器展示，实际产物标题来自 Agent 的结构化结果。
        config.pop("title", None)
    return {
        "id": raw.get("id"),
        "type": raw.get("type"),
        "config": normalize_execution_value(config),
        "resource_fingerprint": normalize_execution_value(resource_fingerprint),
    }


def edge_execution_payload(edge: WorkflowEdge | Mapping[str, Any]) -> dict[str, Any]:
    """Pick the edge fields that influence a target's input and conditions."""
    raw = edge.model_dump(mode="json") if isinstance(edge, BaseModel) else dict(edge)
    return {
        "id": raw.get("id"),
        "source": raw.get("source"),
        "target": raw.get("target"),
        "condition": normalize_execution_value(raw.get("condition")),
    }


def default_workflow_graph(workflow_type: WorkflowType, default_backend: str) -> WorkflowGraph:
    if workflow_type is WorkflowType.operation:
        return WorkflowGraph()
    return WorkflowGraph.model_validate(
        {
            "nodes": [
                {
                    "id": "markdown-output",
                    "type": "output",
                    "name": "Markdown 主报告",
                    "position": {"x": 160, "y": 120},
                    "config": {
                        "format": "markdown",
                        "title": "总结报告",
                        "path": "reports/index.md",
                        "prompt": "根据全部上游节点输出生成结构清晰的 Markdown 主报告；返回 title、summary、content，content 必须是完整 Markdown。",
                        "backend_key": default_backend,
                        "system_role": "summary_markdown",
                    },
                },
                {
                    "id": "html-output",
                    "type": "output",
                    "name": "HTML 派生报告",
                    "position": {"x": 460, "y": 120},
                    "config": {
                        "format": "html",
                        "title": "总结报告 HTML",
                        "path": "reports/index.html",
                        "prompt": "只根据 Markdown 主产物生成完整 HTML 文档；返回 title、summary、content，content 必须包含 html 或 body 标签、内联 CSS、无外链脚本。",
                        "backend_key": default_backend,
                        "skill_names": ["design_html_report"],
                        "system_role": "summary_html",
                    },
                },
            ],
            "edges": [
                {
                    "id": "markdown-to-html",
                    "source": "markdown-output",
                    "target": "html-output",
                    "system_role": "summary_markdown_to_html",
                }
            ],
        }
    )


def normalize_summary_graph(graph: WorkflowGraph, default_backend: str) -> WorkflowGraph:
    """Normalize an imported summary graph to its two system-owned outputs."""
    raw_nodes = [node.model_dump(mode="json") for node in graph.nodes]
    raw_edges = [edge.model_dump(mode="json") for edge in graph.edges]

    marked = [node for node in raw_nodes if node["type"] == "output" and node["config"].get("system_role")]
    markdown_marked = [node for node in marked if node["config"].get("system_role") == "summary_markdown"]
    html_marked = [node for node in marked if node["config"].get("system_role") == "summary_html"]
    if marked and (len(markdown_marked) != 1 or len(html_marked) != 1 or len(marked) != 2):
        raise ValidationError("总结类工作流导入失败：必须只有一个 Markdown 和一个 HTML 系统输出节点")

    ordinary_outputs = [node for node in raw_nodes if node["type"] == "output" and not node["config"].get("system_role")]
    if marked and ordinary_outputs:
        raise ValidationError("总结类工作流导入失败：不能同时包含系统总结节点和普通输出节点")
    if not marked:
        markdown_candidates = [node for node in ordinary_outputs if node["config"].get("format") == "markdown"]
        html_candidates = [node for node in ordinary_outputs if node["config"].get("format") == "html"]
        if len(markdown_candidates) > 1:
            raise ValidationError("总结类工作流导入失败：存在多个 Markdown 输出节点，无法确定主报告")
        if len(html_candidates) > 1:
            raise ValidationError("总结类工作流导入失败：存在多个 HTML 输出节点，无法确定派生报告")
        markdown_marked = markdown_candidates[:1]
        html_marked = html_candidates[:1]

    defaults = default_workflow_graph(WorkflowType.summary, default_backend).model_dump(mode="json")["nodes"]
    markdown = markdown_marked[0] if markdown_marked else defaults[0]
    html = html_marked[0] if html_marked else defaults[1]
    selected_ids = {node["id"] for node in markdown_marked + html_marked}
    used_node_ids = {node["id"] for node in raw_nodes if node["id"] not in selected_ids}

    def unique_node_id(base: str) -> str:
        value = base
        suffix = 2
        while value in used_node_ids:
            value = f"{base}-{suffix}"
            suffix += 1
        used_node_ids.add(value)
        return value

    if not markdown_marked:
        markdown["id"] = unique_node_id(markdown["id"])
    else:
        used_node_ids.add(markdown["id"])
    if not html_marked:
        html["id"] = unique_node_id(html["id"])
    else:
        used_node_ids.add(html["id"])
    markdown["config"]["system_role"] = "summary_markdown"
    markdown["config"]["format"] = "markdown"
    html["config"]["system_role"] = "summary_html"
    html["config"]["format"] = "html"

    summary_ids = {markdown["id"], html["id"]}
    business_nodes = [node for node in raw_nodes if node["id"] not in summary_ids]
    business_ids = {node["id"] for node in business_nodes}
    business_edges = [
        edge
        for edge in raw_edges
        if edge["source"] in business_ids
        and edge["target"] in business_ids
    ]
    incoming_markdown = [
        edge
        for edge in raw_edges
        if edge["source"] in business_ids and edge["target"] == markdown["id"]
    ]
    preserved_edges = business_edges + incoming_markdown
    existing_markdown_sources = {edge["source"] for edge in incoming_markdown}
    outgoing_business = {edge["source"] for edge in business_edges}
    used_edge_ids = {edge["id"] for edge in preserved_edges}

    def unique_edge_id(base: str) -> str:
        value = base
        suffix = 2
        while value in used_edge_ids:
            value = f"{base}-{suffix}"
            suffix += 1
        used_edge_ids.add(value)
        return value

    for node in business_nodes:
        if node["id"] in outgoing_business or node["id"] in existing_markdown_sources:
            continue
        preserved_edges.append({
            "id": unique_edge_id(f"{node['id']}-{markdown['id']}"),
            "source": node["id"],
            "target": markdown["id"],
            "condition": None,
            "system_role": None,
        })

    preserved_edges.append({
        "id": unique_edge_id("markdown-to-html"),
        "source": markdown["id"],
        "target": html["id"],
        "condition": None,
        "system_role": "summary_markdown_to_html",
    })
    return WorkflowGraph.model_validate({"nodes": business_nodes + [markdown, html], "edges": preserved_edges})
