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
    graph = WorkflowGraph.model_validate({"nodes": [{"id": "a", "type": "agent", "name": "A", "position": {"x": 0, "y": 0}, "config": {"prompt": "review", "backend_key": "claude", "skill_names": ["review"], "timeout_seconds": 1200}}]})
    node = graph.nodes[0]
    agent = FakeAgent()
    result = await WorkflowNodeHandlers(agent_service=agent, scripts=SimpleNamespace(), skill_service=FakeSkills()).execute(node, NodeExecutionContext(actor="root", workflow={"workflow_key": "w", "profile_key": "p"}, run_id="r", input={}, task=None, nodes={}, graph=graph))
    assert agent.kwargs["prompt"].index("[技能：review]") < agent.kwargs["prompt"].index("[任务指令]")
    assert agent.kwargs["timeout"] == 1200
    assert result.output == {"text": "ok"}


@pytest.mark.asyncio
async def test_agent_handler_injects_current_task_context():
    graph = WorkflowGraph.model_validate({"nodes": [{"id": "a", "type": "agent", "name": "A", "position": {"x": 0, "y": 0}, "config": {"prompt": "分析当前报表", "backend_key": "claude"}}]})
    node = graph.nodes[0]
    agent = FakeAgent()
    task = {
        "task_key": "task-1",
        "task_version": "v2",
        "type": "fine-report",
        "payload": {"report_id": "report-7", "cpt_file_path": "/reports/demo.cpt"},
    }

    await WorkflowNodeHandlers(agent_service=agent, scripts=SimpleNamespace(), skill_service=FakeSkills()).execute(
        node,
        NodeExecutionContext(
            actor="root",
            workflow={"workflow_key": "w", "profile_key": "p"},
            run_id="r",
            input={},
            task=task,
            nodes={},
            graph=graph,
        ),
    )

    assert "[当前任务上下文]" in agent.kwargs["prompt"]
    assert "task-1" in agent.kwargs["prompt"]
    assert "v2" in agent.kwargs["prompt"]
    assert "/reports/demo.cpt" in agent.kwargs["prompt"]
