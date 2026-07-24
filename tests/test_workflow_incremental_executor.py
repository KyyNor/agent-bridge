from __future__ import annotations

import pytest

from agent_bridge.automation.workflows.definition import WorkflowGraph
from agent_bridge.automation.workflows.executor import WorkflowDagExecutor
from agent_bridge.automation.workflows.handlers import NodeExecutionResult
from agent_bridge.automation.workflows.incremental import IncrementalPlan, NodePlan


class Store:
    def __init__(self, artifacts=None):
        self.node_runs = {}
        self.associations = []
        self.artifacts = artifacts or {}

    def create_workflow_node_runs(self, run_id, nodes):
        self.node_runs = {node["node_id"]: {"status": "pending"} for node in nodes}

    def start_workflow_node_run(self, run_id, node_id, condition_results):
        self.node_runs[node_id].update(status="running", condition_results=condition_results)

    def finish_workflow_node_run(self, run_id, node_id, *, status, output=None, **kwargs):
        self.node_runs[node_id].update(status=status, output=output or {}, **kwargs)

    def associate_workflow_run_artifacts(self, run_id, node_id, artifact_ids, **kwargs):
        self.associations.extend((run_id, node_id, artifact_id, kwargs) for artifact_id in artifact_ids)

    def get_workflow_artifact(self, artifact_id):
        return self.artifacts.get(artifact_id)


def _graph(node_types, edges):
    nodes = []
    for node_id, node_type in node_types:
        node = {"id": node_id, "type": node_type, "name": node_id, "position": {"x": 0, "y": 0}}
        if node_type == "script":
            node["config"] = {"script_key": node_id, "params": {}}
        elif node_type == "agent":
            node["config"] = {"prompt": node_id, "backend_key": "claude"}
        nodes.append(node)
    return WorkflowGraph.model_validate({"nodes": nodes, "edges": [
        {"id": f"{source}-{target}", "source": source, "target": target}
        for source, target in edges
    ]})


def _plan(*node_plans, mode="incremental"):
    return IncrementalPlan(
        workflow_key="wf", workflow_revision_no=2, workflow_content_hash="current",
        task_version="v1", mode=mode, baseline_run_id="old-run", nodes=node_plans,
        affected_node_ids=tuple(node.node_id for node in node_plans if node.action == "execute"),
        reusable_node_ids=tuple(node.node_id for node in node_plans if node.action == "reuse"),
        reasons={node.node_id: node.reason for node in node_plans}, warnings=(),
    )


def _node(node_id, action, *, output=None, artifact_ids=(), reason=None):
    return NodePlan(
        node_id=node_id, action=action, reason=reason or ("fingerprint_match" if action == "reuse" else "upstream_execute"),
        node_fingerprint=f"fingerprint-{node_id}", source_run_id="old-run" if action == "reuse" else None,
        source_node_id=node_id if action == "reuse" else None,
        source_node_fingerprint=f"old-{node_id}" if action == "reuse" else None,
        output_json=output, artifact_ids=artifact_ids,
    )


@pytest.mark.asyncio
async def test_incremental_executor_reuses_outputs_without_calling_handlers_and_persists_lineage():
    graph = _graph([(node_id, "script") for node_id in "abcd"], [("a", "b"), ("b", "c"), ("c", "d")])
    calls = []

    class Handlers:
        async def execute(self, node, context):
            calls.append(node.id)
            if node.id == "c":
                assert context.nodes["b"]["output"] == {"value": "historic-b"}
                return NodeExecutionResult(output={"value": "fresh-c"})
            assert context.nodes["c"]["output"] == {"value": "fresh-c"}
            return NodeExecutionResult(output={"value": "fresh-d"})

    store = Store()
    result = await WorkflowDagExecutor(store=store, handlers=Handlers()).run(
        workflow={"workflow_key": "wf", "profile_key": "p", "definition": graph}, run_id="new-run",
        input_data={}, actor="root", plan=_plan(
            _node("a", "reuse", output={"value": "historic-a"}),
            _node("b", "reuse", output={"value": "historic-b"}),
            _node("c", "execute"), _node("d", "execute"),
        ),
    )

    assert result.status == "completed"
    assert calls == ["c", "d"]
    assert store.node_runs["a"]["action"] == "reuse"
    assert store.node_runs["a"]["reuse_reason"] == "fingerprint_match"
    assert store.node_runs["a"]["source_run_id"] == "old-run"
    assert store.node_runs["a"]["output"] == {"value": "historic-a"}
    assert store.node_runs["c"]["action"] == "execute"


@pytest.mark.asyncio
async def test_executed_get_task_refreshes_task_for_downstream_handler_context():
    graph = _graph([("task", "get_task"), ("work", "agent")], [("task", "work")])
    calls = []

    class Handlers:
        async def execute(self, node, context):
            calls.append(node.id)
            if node.id == "task":
                assert context.task is None
                return NodeExecutionResult(output={"task": {"task_key": "t1", "task_version": "v1"}})
            assert context.task == {"task_key": "t1", "task_version": "v1"}
            assert context.execution_mode == "incremental"
            return NodeExecutionResult(output={"done": True})

    result = await WorkflowDagExecutor(store=Store(), handlers=Handlers()).run(
        workflow={"workflow_key": "wf", "profile_key": "p", "definition": graph}, run_id="new-run",
        input_data={}, actor="root", plan=_plan(
            _node("task", "execute"),
            _node("work", "execute"),
        ),
    )

    assert result.task == {"task_key": "t1", "task_version": "v1"}
    assert calls == ["task", "work"]


@pytest.mark.asyncio
async def test_invalid_reused_artifact_falls_back_to_execute_at_that_node():
    graph = _graph([("output", "script")], [])
    calls = []

    class Handlers:
        async def execute(self, node, context):
            calls.append(node.id)
            return NodeExecutionResult(output={"value": "fresh"})

    store = Store(artifacts={})
    await WorkflowDagExecutor(store=store, handlers=Handlers()).run(
        workflow={"workflow_key": "wf", "profile_key": "p", "definition": graph}, run_id="new-run",
        input_data={}, actor="root", plan=_plan(
            _node("output", "reuse", output={"artifact_ids": ["gone"]}, artifact_ids=("gone",)),
        ),
    )

    assert calls == ["output"]
    assert store.node_runs["output"]["action"] == "execute"
    assert store.node_runs["output"]["reuse_reason"] == "source_artifact_missing"


@pytest.mark.asyncio
async def test_force_full_plan_calls_every_handler():
    graph = _graph([(node_id, "script") for node_id in "ab"], [("a", "b")])
    calls = []

    class Handlers:
        async def execute(self, node, context):
            calls.append(node.id)
            return NodeExecutionResult(output={"node": node.id})

    await WorkflowDagExecutor(store=Store(), handlers=Handlers()).run(
        workflow={"workflow_key": "wf", "profile_key": "p", "definition": graph}, run_id="new-run",
        input_data={}, actor="root", plan=_plan(_node("a", "execute", reason="force_full"), _node("b", "execute", reason="force_full"), mode="force_full"),
    )

    assert calls == ["a", "b"]


@pytest.mark.asyncio
async def test_conditional_skipped_branch_does_not_invalidate_reused_merge_node():
    graph = WorkflowGraph.model_validate({
        "nodes": [
            {"id": "a", "type": "script", "name": "a", "position": {"x": 0, "y": 0}, "config": {"script_key": "a"}},
            {"id": "b", "type": "script", "name": "b", "position": {"x": 1, "y": 0}, "config": {"script_key": "b"}},
            {"id": "x", "type": "script", "name": "x", "position": {"x": 1, "y": 1}, "config": {"script_key": "x"}},
            {"id": "merge", "type": "script", "name": "merge", "position": {"x": 2, "y": 0}, "config": {"script_key": "merge"}},
        ],
        "edges": [
            {"id": "a-b", "source": "a", "target": "b", "condition": {"field": "nodes.a.output.route", "operator": "equals", "value": "primary"}},
            {"id": "a-x", "source": "a", "target": "x", "condition": {"field": "nodes.a.output.route", "operator": "equals", "value": "secondary"}},
            {"id": "b-merge", "source": "b", "target": "merge"},
            {"id": "x-merge", "source": "x", "target": "merge"},
        ],
    })
    calls = []

    class Handlers:
        async def execute(self, node, context):
            calls.append(node.id)
            return NodeExecutionResult(output={"route": "primary"})

    store = Store()
    result = await WorkflowDagExecutor(store=store, handlers=Handlers()).run(
        workflow={"workflow_key": "wf", "profile_key": "p", "definition": graph}, run_id="new-run",
        input_data={}, actor="root", plan=_plan(
            NodePlan("a", "execute", "task_lease_must_refresh", "a", invalidates_downstream=False),
            _node("b", "reuse", output={"value": "historic-b"}),
            NodePlan("x", "execute", "baseline_node_not_completed", "x", runtime_deferred=True, invalidates_downstream=True),
            NodePlan("merge", "reuse", "fingerprint_match", "merge", source_run_id="old-run", source_node_id="merge", output_json={"value": "historic-merge"}, runtime_deferred=True),
        ),
    )

    assert result.status == "completed"
    assert calls == ["a"]
    assert store.node_runs["x"]["reuse_reason"] == "condition_not_matched"
    assert store.node_runs["b"]["action"] == "reuse"
    assert store.node_runs["merge"]["action"] == "reuse"


@pytest.mark.asyncio
async def test_conditional_active_branch_invalidates_reused_merge_node_at_runtime():
    graph = WorkflowGraph.model_validate({
        "nodes": [
            {"id": "a", "type": "script", "name": "a", "position": {"x": 0, "y": 0}, "config": {"script_key": "a"}},
            {"id": "b", "type": "script", "name": "b", "position": {"x": 1, "y": 0}, "config": {"script_key": "b"}},
            {"id": "x", "type": "script", "name": "x", "position": {"x": 1, "y": 1}, "config": {"script_key": "x"}},
            {"id": "merge", "type": "script", "name": "merge", "position": {"x": 2, "y": 0}, "config": {"script_key": "merge"}},
        ],
        "edges": [
            {"id": "a-b", "source": "a", "target": "b", "condition": {"field": "nodes.a.output.route", "operator": "equals", "value": "primary"}},
            {"id": "a-x", "source": "a", "target": "x", "condition": {"field": "nodes.a.output.route", "operator": "equals", "value": "secondary"}},
            {"id": "b-merge", "source": "b", "target": "merge"},
            {"id": "x-merge", "source": "x", "target": "merge"},
        ],
    })
    calls = []

    class Handlers:
        async def execute(self, node, context):
            calls.append(node.id)
            return NodeExecutionResult(output={"route": "secondary"} if node.id == "a" else {"value": node.id})

    store = Store()
    await WorkflowDagExecutor(store=store, handlers=Handlers()).run(
        workflow={"workflow_key": "wf", "profile_key": "p", "definition": graph}, run_id="new-run",
        input_data={}, actor="root", plan=_plan(
            NodePlan("a", "execute", "task_lease_must_refresh", "a", invalidates_downstream=False),
            _node("b", "reuse", output={"value": "historic-b"}),
            NodePlan("x", "execute", "baseline_node_not_completed", "x", runtime_deferred=True, invalidates_downstream=True),
            NodePlan("merge", "reuse", "fingerprint_match", "merge", source_run_id="old-run", source_node_id="merge", output_json={"value": "historic-merge"}, runtime_deferred=True),
        ),
    )

    assert calls == ["a", "x", "merge"]
    assert store.node_runs["b"]["reuse_reason"] == "condition_not_matched"
    assert store.node_runs["merge"]["action"] == "execute"
    assert store.node_runs["merge"]["reuse_reason"] == "upstream_execute"
