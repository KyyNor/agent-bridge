import pytest

from agent_bridge.automation.workflows.definition import WorkflowGraph
from agent_bridge.automation.workflows.executor import WorkflowDagExecutor
from agent_bridge.automation.workflows.handlers import NodeExecutionResult


class Store:
    def create_workflow_node_runs(self, *args): pass
    def start_workflow_node_run(self, *args): pass
    def finish_workflow_node_run(self, *args, **kwargs): pass


class Handlers:
    async def execute(self, node, context):
        return NodeExecutionResult(output={"text": node.id})


@pytest.mark.asyncio
async def test_executor_runs_parallel_nodes_then_join():
    graph = WorkflowGraph.model_validate({"nodes": [
        {"id": item, "type": "agent", "name": item, "position": {"x": 0, "y": 0}, "config": {"prompt": item, "backend_key": "claude"}}
        for item in ("start", "left", "right", "join")
    ], "edges": [{"id": "sl", "source": "start", "target": "left"}, {"id": "sr", "source": "start", "target": "right"}, {"id": "lj", "source": "left", "target": "join"}, {"id": "rj", "source": "right", "target": "join"}]})
    result = await WorkflowDagExecutor(store=Store(), handlers=Handlers()).run(workflow={"workflow_key": "w", "profile_key": "p", "definition": graph}, run_id="r", input_data={}, actor="root")
    assert result.status == "completed"
    assert result.node_statuses["join"] == "completed"
