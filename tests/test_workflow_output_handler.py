import pytest

from agent_bridge.agent_runtime.service import AgentRunResult
from agent_bridge.automation.workflows.definition import WorkflowGraph
from agent_bridge.automation.workflows.handlers import NodeExecutionContext
from agent_bridge.automation.workflows.output_handler import OutputHandler


class Agent:
    async def run(self, **kwargs):
        self.kwargs = kwargs
        return AgentRunResult(ok=True, result={"title": "T", "summary": "S", "content": "# Report"}, run_key="agent_1")


class HtmlAgent(Agent):
    async def run(self, **kwargs):
        self.kwargs = kwargs
        return AgentRunResult(ok=True, result={"title": "T", "summary": "S", "content": "<html><body>Report</body></html>"}, run_key="agent_2")


class Skills:
    def get_skill(self, actor, skill_name): return {"prompt": ""}


class Workflows:
    def save_artifact(self, **kwargs):
        self.kwargs = kwargs
        return {"artifact_id": "artifact_markdown"}


class FailingWorkflows:
    def save_artifact(self, **kwargs): raise RuntimeError("artifact store unavailable")


@pytest.mark.asyncio
async def test_markdown_output_injects_ancestors_and_saves_artifact():
    graph = WorkflowGraph.model_validate({"nodes": [
        {"id": "analysis", "type": "agent", "name": "Analysis", "position": {"x": 0, "y": 0}, "config": {"prompt": "analyze", "backend_key": "claude"}},
        {"id": "out", "type": "output", "name": "Out", "position": {"x": 1, "y": 0}, "config": {"format": "markdown", "title": "T", "path": "reports/index.md", "prompt": "render", "backend_key": "claude", "timeout_seconds": 1200}},
    ], "edges": [{"id": "analysis-out", "source": "analysis", "target": "out"}]})
    node = graph.nodes[1]
    agent = Agent()
    workflows = Workflows()
    result = await OutputHandler(agent_service=agent, skill_service=Skills(), workflow_service=workflows).execute(node, NodeExecutionContext(actor="root", workflow={"workflow_key": "w", "profile_key": "p"}, run_id="r", input={}, task={"task_key": "task-1", "task_version": "v2", "payload": {"report_id": "report-7", "cpt_file_path": "/reports/demo.cpt"}}, nodes={"analysis": {"output": {"text": "x"}}}, graph=graph, node_fingerprint="current-fingerprint"))
    assert "[当前任务上下文]" in agent.kwargs["prompt"]
    assert "report-7" in agent.kwargs["prompt"]
    assert "/reports/demo.cpt" in agent.kwargs["prompt"]
    assert "[上游节点输出]" in agent.kwargs["prompt"]
    assert result.artifact_ids == ["artifact_markdown"]
    assert agent.kwargs["timeout"] == 1200
    assert workflows.kwargs["producer_node_id"] == "out"
    assert workflows.kwargs["producer_node_fingerprint"] == "current-fingerprint"


@pytest.mark.asyncio
async def test_html_save_failure_becomes_warning_but_markdown_save_failure_raises():
    graph = WorkflowGraph.model_validate({"nodes": [
        {"id": "markdown", "type": "output", "name": "Markdown", "position": {"x": 0, "y": 0}, "config": {"format": "markdown", "title": "M", "path": "m.md", "prompt": "render", "backend_key": "claude"}},
        {"id": "html", "type": "output", "name": "HTML", "position": {"x": 1, "y": 0}, "config": {"format": "html", "title": "H", "path": "h.html", "prompt": "render", "backend_key": "claude"}},
    ], "edges": [{"id": "m-h", "source": "markdown", "target": "html"}]})
    context = NodeExecutionContext(actor="root", workflow={"workflow_key": "w", "profile_key": "p"}, run_id="r", input={}, task=None, nodes={"markdown": {"type": "output", "format": "markdown", "output": {"title": "M", "summary": "S", "content": "# M"}}}, graph=graph)
    html_result = await OutputHandler(agent_service=HtmlAgent(), skill_service=Skills(), workflow_service=FailingWorkflows()).execute(graph.nodes[1], context)
    assert html_result.status == "warning"
    assert "artifact store unavailable" in html_result.error
    with pytest.raises(Exception, match="artifact store unavailable"):
        await OutputHandler(agent_service=Agent(), skill_service=Skills(), workflow_service=FailingWorkflows()).execute(graph.nodes[0], context)


@pytest.mark.asyncio
async def test_output_prompts_follow_graph_dependencies_only():
    graph = WorkflowGraph.model_validate({"nodes": [
        {"id": "ancestor", "type": "agent", "name": "Ancestor", "position": {"x": 0, "y": 0}, "config": {"prompt": "a", "backend_key": "claude"}},
        {"id": "unrelated", "type": "agent", "name": "Unrelated", "position": {"x": 0, "y": 1}, "config": {"prompt": "u", "backend_key": "claude"}},
        {"id": "markdown", "type": "output", "name": "Markdown", "position": {"x": 1, "y": 0}, "config": {"format": "markdown", "title": "M", "path": "m.md", "prompt": "render", "backend_key": "claude"}},
        {"id": "html", "type": "output", "name": "HTML", "position": {"x": 2, "y": 0}, "config": {"format": "html", "title": "H", "path": "h.html", "prompt": "render", "backend_key": "claude"}},
    ], "edges": [
        {"id": "a-m", "source": "ancestor", "target": "markdown"},
        {"id": "m-h", "source": "markdown", "target": "html"},
    ]})
    nodes = {
        "ancestor": {"type": "agent", "output": {"text": "ancestor-value"}},
        "unrelated": {"type": "agent", "output": {"text": "unrelated-secret"}},
        "markdown": {"type": "output", "format": "markdown", "output": {"title": "M", "summary": "S", "content": "# Main"}},
    }
    context = NodeExecutionContext(actor="root", workflow={"workflow_key": "w", "profile_key": "p"}, run_id="r", input={}, task=None, nodes=nodes, graph=graph)
    markdown_agent = Agent()
    await OutputHandler(agent_service=markdown_agent, skill_service=Skills(), workflow_service=Workflows()).execute(graph.nodes[2], context)
    assert "ancestor-value" in markdown_agent.kwargs["prompt"]
    assert "unrelated-secret" not in markdown_agent.kwargs["prompt"]
    html_agent = HtmlAgent()
    await OutputHandler(agent_service=html_agent, skill_service=Skills(), workflow_service=Workflows()).execute(graph.nodes[3], context)
    assert "# Main" in html_agent.kwargs["prompt"]
    assert "ancestor-value" not in html_agent.kwargs["prompt"]
