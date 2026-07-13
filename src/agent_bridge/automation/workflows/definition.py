from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

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


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str
    backend_key: str
    mcp_enabled: bool = True
    skill_names: list[str] = Field(default_factory=list)
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
