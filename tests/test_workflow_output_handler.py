import pytest

from agent_bridge.agent_runtime.service import AgentRunResult
from agent_bridge.automation.workflows.definition import WorkflowGraph
from agent_bridge.automation.workflows.handlers import NodeExecutionContext
from agent_bridge.automation.workflows.output_handler import OutputHandler


class Agent:
    async def run(self, **kwargs):
        self.kwargs = kwargs
        return AgentRunResult(ok=True, result={"title": "T", "summary": "S", "content": "# Report"}, run_key="agent_1")


class Skills:
    def get_skill(self, actor, skill_name): return {"prompt": ""}


class Workflows:
    def save_artifact(self, **kwargs): return {"artifact_id": "artifact_markdown"}


@pytest.mark.asyncio
async def test_markdown_output_injects_ancestors_and_saves_artifact():
    node = WorkflowGraph.model_validate({"nodes": [{"id": "out", "type": "output", "name": "Out", "position": {"x": 0, "y": 0}, "config": {"format": "markdown", "title": "T", "path": "reports/index.md", "prompt": "render", "backend_key": "claude"}}]}).nodes[0]
    agent = Agent()
    result = await OutputHandler(agent_service=agent, skill_service=Skills(), workflow_service=Workflows()).execute(node, NodeExecutionContext(actor="root", workflow={"workflow_key": "w", "profile_key": "p"}, run_id="r", input={}, task=None, nodes={"analysis": {"output": {"text": "x"}}}))
    assert "[上游节点输出]" in agent.kwargs["prompt"]
    assert result.artifact_ids == ["artifact_markdown"]
