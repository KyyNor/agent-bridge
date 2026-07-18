from __future__ import annotations

from copy import deepcopy

import pytest

from agent_bridge.automation.workflows.incremental import WorkflowIncrementalPlanner


def _workflow(changed: str | None = None, *, position_only: bool = False, add_node: bool = False, edge_change: bool = False):
    nodes = [
        {"id": node_id, "type": "script", "name": node_id, "position": {"x": index, "y": 0}, "config": {"script_key": node_id}}
        for index, node_id in enumerate("abcd")
    ]
    if changed and not position_only:
        next(item for item in nodes if item["id"] == changed)["config"]["script_key"] += "-v2"
    if position_only:
        nodes[0]["position"] = {"x": 900, "y": 700}
    edges = [{"id": f"{source}-{target}", "source": source, "target": target} for source, target in zip("abc", "bcd")]
    if edge_change:
        edges[-1] = {"id": "x-c-d", "source": "c", "target": "d", "condition": {"field": "ok", "operator": "eq", "value": True}}
    if add_node:
        nodes.append({"id": "e", "type": "script", "name": "e", "position": {"x": 4, "y": 0}, "config": {"script_key": "e"}})
        edges.append({"id": "d-e", "source": "d", "target": "e"})
    return {"workflow_key": "wf", "profile_key": "p", "definition": {"nodes": nodes, "edges": edges}}


def _baseline(planner: WorkflowIncrementalPlanner, workflow: dict, *, run_id: str = "baseline", task_version: str = "v1"):
    graph = workflow["definition"]
    runtime = {node["id"]: "runtime-v1" for node in graph["nodes"]}
    node_runs = [{
        "run_id": run_id,
        "node_id": node["id"],
        "status": "completed",
        "node_fingerprint": planner.node_fingerprint(node, runtime_fingerprint=runtime),
        "output": {"node": node["id"]},
        "artifact_ids": [],
    } for node in graph["nodes"]]
    run = {"id": 1, "run_id": run_id, "workflow_key": "wf", "profile_key": "p", "task_key": "task", "task_version": task_version, "status": "completed", "finished_at": "2026-01-01T00:00:00", "definition_snapshot": deepcopy(graph)}
    return run, node_runs, runtime


def _plan(workflow: dict, *, baseline_workflow: dict | None = None, task_version: str = "v1", node_runs=None, run_id: str = "baseline", runtime=None):
    planner = WorkflowIncrementalPlanner()
    source = baseline_workflow or _workflow()
    run, baseline_nodes, default_runtime = _baseline(planner, source, run_id=run_id, task_version="v1")
    return planner.build(
        workflow=workflow,
        current_revision={"revision_no": 2, "content_hash": "v2"},
        task={"task_key": "task", "task_version": task_version},
        mode="incremental",
        baseline_run=run,
        baseline_node_runs=node_runs or baseline_nodes,
        baseline_artifacts=[],
        runtime_fingerprint=runtime or default_runtime,
    )


@pytest.mark.parametrize(
    ("changed", "expected_execute"),
    [("c", ["c", "d"]), ("b", ["b", "c", "d"]), ("d", ["d"])],
)
def test_acceptance_direct_node_changes_propagate_downstream(changed, expected_execute):
    plan = _plan(_workflow(changed), baseline_workflow=_workflow())
    assert [node.node_id for node in plan.nodes if node.action == "execute"] == expected_execute
    assert [node.node_id for node in plan.nodes if node.action == "reuse"] == [node for node in "abcd" if node not in expected_execute]


def test_acceptance_task_version_change_is_full_execution():
    plan = _plan(_workflow(), task_version="v2")
    assert all(node.action == "execute" for node in plan.nodes)


def test_acceptance_position_only_change_is_reusable():
    plan = _plan(_workflow(position_only=True), baseline_workflow=_workflow())
    assert all(node.action == "reuse" for node in plan.nodes)


def test_acceptance_edge_change_propagates_from_target():
    plan = _plan(_workflow(edge_change=True), baseline_workflow=_workflow())
    assert [node.node_id for node in plan.nodes if node.action == "execute"] == ["d"]


def test_acceptance_added_node_executes_without_cross_run_mix():
    plan = _plan(_workflow(add_node=True), baseline_workflow=_workflow())
    assert plan.nodes[-1].node_id == "e"
    assert plan.nodes[-1].action == "execute"
    assert plan.baseline_run_id == "baseline"


def test_acceptance_missing_output_restarts_at_invalid_node_and_downstream():
    planner = WorkflowIncrementalPlanner()
    baseline = _workflow()
    run, node_runs, runtime = _baseline(planner, baseline)
    node_runs[2]["output"] = None
    plan = _plan(_workflow(), node_runs=node_runs, runtime=runtime)
    assert [node.node_id for node in plan.nodes if node.action == "execute"] == ["c", "d"]


def test_acceptance_single_baseline_uses_newest_database_run_id_on_tie():
    planner = WorkflowIncrementalPlanner()
    workflow = _workflow()
    old, nodes, runtime = _baseline(planner, workflow, run_id="old")
    new, new_nodes, _ = _baseline(planner, workflow, run_id="new")
    old["id"], new["id"] = 1, 2
    plan = planner.build(
        workflow=workflow,
        current_revision={"revision_no": 2, "content_hash": "v2"},
        task={"task_key": "task", "task_version": "v1"},
        mode="incremental",
        baseline_run=[old, new],
        baseline_node_runs={"old": nodes, "new": new_nodes},
        baseline_artifacts={"old": [], "new": []},
        runtime_fingerprint=runtime,
    )
    assert plan.baseline_run_id == "new"


def test_acceptance_force_full_bypasses_reuse_and_preview_has_reasons():
    planner = WorkflowIncrementalPlanner()
    workflow = _workflow()
    run, nodes, runtime = _baseline(planner, workflow)
    plan = planner.build(
        workflow=workflow,
        current_revision={"revision_no": 2, "content_hash": "v2"},
        task={"task_key": "task", "task_version": "v1"},
        mode="force_full",
        baseline_run=run,
        baseline_node_runs=nodes,
        baseline_artifacts=[],
        runtime_fingerprint=runtime,
    )
    assert all(node.action == "execute" and node.reason == "force_full" for node in plan.nodes)
    assert set(plan.reasons) == set("abcd")
