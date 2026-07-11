import asyncio

import pytest

from agent_bridge.automation.workflows.definition import WorkflowGraph
from agent_bridge.automation.workflows.executor import WorkflowDagExecutor
from agent_bridge.automation.workflows.handlers import NodeExecutionError, NodeExecutionResult
from agent_bridge.automation.workflows.validation import WorkflowDefinitionValidationError


class Store:
    def __init__(self):
        self.statuses = {}
        self.outputs = {}

    def create_workflow_node_runs(self, run_id, nodes):
        self.statuses.update({node["node_id"]: "pending" for node in nodes})

    def start_workflow_node_run(self, run_id, node_id, condition_results):
        self.statuses[node_id] = "running"

    def finish_workflow_node_run(self, run_id, node_id, *, status, output=None, **kwargs):
        self.statuses[node_id] = status
        self.outputs[node_id] = output or {}


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


@pytest.mark.asyncio
async def test_executor_persists_every_done_result_before_batch_failure():
    graph = WorkflowGraph.model_validate({"nodes": [
        {"id": item, "type": "agent", "name": item, "position": {"x": 0, "y": 0}, "config": {"prompt": item, "backend_key": "claude"}}
        for item in ("ok", "fail")
    ]})
    store = Store()

    class BatchHandlers:
        def __init__(self):
            self.arrived = 0
            self.release = asyncio.Event()

        async def execute(self, node, context):
            self.arrived += 1
            if self.arrived == 2:
                self.release.set()
            await self.release.wait()
            if node.id == "fail":
                raise NodeExecutionError(node.id, "boom")
            return NodeExecutionResult(output={"text": "saved"})

    result = await WorkflowDagExecutor(store=store, handlers=BatchHandlers()).run(workflow={"workflow_key": "w", "profile_key": "p", "workflow_type": "operation", "definition": graph}, run_id="r", input_data={}, actor="root")
    assert result.status == "failed"
    assert store.statuses == {"ok": "completed", "fail": "failed"}
    assert store.outputs["ok"] == {"text": "saved"}


@pytest.mark.asyncio
async def test_no_task_cancels_started_nodes_and_finishes_pending(monkeypatch):
    monkeypatch.setattr("agent_bridge.automation.workflows.executor.validate_graph", lambda graph, workflow_type: None)
    graph = WorkflowGraph.model_validate({"nodes": [
        {"id": "task", "type": "get_task", "name": "Task", "position": {"x": 0, "y": 0}},
        {"id": "slow", "type": "agent", "name": "Slow", "position": {"x": 1, "y": 0}, "config": {"prompt": "slow", "backend_key": "claude"}},
        {"id": "after", "type": "agent", "name": "After", "position": {"x": 2, "y": 0}, "config": {"prompt": "after", "backend_key": "claude"}},
    ], "edges": [{"id": "task-after", "source": "task", "target": "after"}]})

    class NoTaskHandlers:
        async def execute(self, node, context):
            if node.id == "task":
                return NodeExecutionResult(output={"task": None})
            await asyncio.Event().wait()

    store = Store()
    result = await WorkflowDagExecutor(store=store, handlers=NoTaskHandlers()).run(workflow={"workflow_key": "w", "profile_key": "p", "definition": graph}, run_id="r", input_data={}, actor="root")
    assert result.status == "no_task"
    assert store.statuses == {"task": "completed", "slow": "cancelled", "after": "skipped"}


@pytest.mark.asyncio
async def test_summary_output_keeps_markdown_when_html_warns():
    graph = WorkflowGraph.model_validate({"nodes": [
        {"id": "markdown-output", "type": "output", "name": "Markdown", "position": {"x": 0, "y": 0}, "config": {"format": "markdown", "title": "M", "path": "m.md", "prompt": "m", "backend_key": "claude"}},
        {"id": "html-output", "type": "output", "name": "HTML", "position": {"x": 1, "y": 0}, "config": {"format": "html", "title": "H", "path": "h.html", "prompt": "h", "backend_key": "claude"}},
    ], "edges": [{"id": "markdown-to-html", "source": "markdown-output", "target": "html-output"}]})

    class SummaryHandlers:
        async def execute(self, node, context):
            if node.id == "markdown-output":
                return NodeExecutionResult(output={"content": "# Main", "artifact_ids": ["md-1"]})
            return NodeExecutionResult(status="warning", error="html failed")

    result = await WorkflowDagExecutor(store=Store(), handlers=SummaryHandlers()).run(workflow={"workflow_key": "w", "profile_key": "p", "workflow_type": "summary", "definition": graph}, run_id="r", input_data={}, actor="root")
    assert result.status == "completed"
    assert result.output["markdown-output"]["content"] == "# Main"
    assert result.warnings == ["html failed"]


@pytest.mark.asyncio
async def test_executor_validates_graph_and_detects_stalled_schedule(monkeypatch):
    graph = WorkflowGraph.model_validate({"nodes": [
        {"id": item, "type": "agent", "name": item, "position": {"x": 0, "y": 0}, "config": {"prompt": item, "backend_key": "claude"}}
        for item in ("a", "b")
    ], "edges": [{"id": "a-b", "source": "a", "target": "b"}, {"id": "b-a", "source": "b", "target": "a"}]})
    executor = WorkflowDagExecutor(store=Store(), handlers=Handlers())
    with pytest.raises(WorkflowDefinitionValidationError):
        await executor.run(workflow={"workflow_key": "w", "profile_key": "p", "definition": graph}, run_id="r1", input_data={}, actor="root")
    monkeypatch.setattr("agent_bridge.automation.workflows.executor.validate_graph", lambda graph, workflow_type: None)
    result = await executor.run(workflow={"workflow_key": "w", "profile_key": "p", "definition": graph}, run_id="r2", input_data={}, actor="root")
    assert result.status == "failed"
    assert "调度停滞" in result.error
