from types import SimpleNamespace

import pytest

from agent_bridge.agent_runtime.service import AgentRunResult
from agent_bridge.automation.workflows.definition import WorkflowGraph
from agent_bridge.automation.workflows.handlers import NodeExecutionContext, WorkflowNodeHandlers


class FakeAgent:
    async def run(self, **kwargs):
        self.kwargs = kwargs
        return AgentRunResult(ok=True, result="ok", run_key="agent_1")


class FakeSkills:
    def get_skill(self, actor, skill_name): return {"prompt": f"{skill_name} prompt"}


@pytest.mark.asyncio
async def test_agent_handler_prepends_skills_and_wraps_text():
    graph = WorkflowGraph.model_validate({"nodes": [{"id": "a", "type": "agent", "name": "A", "position": {"x": 0, "y": 0}, "config": {"prompt": "review", "backend_key": "claude", "skill_names": ["review"]}}]})
    node = graph.nodes[0]
    agent = FakeAgent()
    result = await WorkflowNodeHandlers(agent_service=agent, scripts=SimpleNamespace(), skill_service=FakeSkills()).execute(node, NodeExecutionContext(actor="root", workflow={"workflow_key": "w", "profile_key": "p"}, run_id="r", input={}, task=None, nodes={}, graph=graph))
    assert agent.kwargs["prompt"].index("[技能：review]") < agent.kwargs["prompt"].index("[任务指令]")
    assert result.output == {"text": "ok"}
