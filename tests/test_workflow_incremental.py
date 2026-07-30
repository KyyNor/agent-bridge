from __future__ import annotations

from copy import deepcopy

import pytest

from agent_bridge.automation.workflows.incremental import WorkflowIncrementalPlanner


def _node(node_id: str, *, node_type: str = "script", config: dict | None = None, x: int = 0):
    return {
        "id": node_id,
        "name": node_id.upper(),
        "type": node_type,
        "position": {"x": x, "y": 0},
        "config": config or {"script_key": f"{node_id}-script"},
    }


def _workflow(nodes: list[dict], edges: list[dict] | None = None):
    return {
        "workflow_key": "wf",
        "profile_key": "p1",
        "definition": {"nodes": nodes, "edges": edges or []},
    }


def _edge(source: str, target: str, *, edge_id: str | None = None, condition=None):
    edge = {"id": edge_id or f"{source}-{target}", "source": source, "target": target}
    if condition is not None:
        edge["condition"] = condition
    return edge


def _task(version: str = "v1"):
    return {"task_key": "task-1", "task_version": version}


def _runtime(nodes: list[dict], version: str = "r1"):
    return {node["id"]: version for node in nodes}


def _baseline(planner, workflow, *, run_id="run-1", task_version="v1", runtime=None):
    graph = workflow["definition"]
    runtime = runtime or _runtime(graph["nodes"])
    node_runs = []
    for node in graph["nodes"]:
        node_runs.append(
            {
                "run_id": run_id,
                "node_id": node["id"],
                "node_type": node["type"],
                "status": "completed",
                "node_fingerprint": planner.node_fingerprint(node, runtime_fingerprint=runtime),
                "output": {"node": node["id"]},
                "artifact_ids": [],
                "condition_results": [],
            }
        )
    return (
        {
            "run_id": run_id,
            "workflow_key": "wf",
            "profile_key": "p1",
            "task_key": "task-1",
            "task_version": task_version,
            "status": "completed",
            "finished_at": "2026-01-01T00:00:00+00:00",
            "definition_snapshot": deepcopy(graph),
        },
        node_runs,
    )


def _plan(planner, workflow, baseline_run, baseline_nodes, *, mode="incremental", task=None, runtime=None, artifacts=None):
    return planner.build(
        workflow=workflow,
        current_revision={"revision_no": 2, "content_hash": "current"},
        task=task or _task(),
        mode=mode,
        baseline_run=baseline_run,
        baseline_node_runs=baseline_nodes,
        baseline_artifacts=artifacts or [],
        runtime_fingerprint=runtime or _runtime(workflow["definition"]["nodes"]),
    )


def test_changed_node_executes_its_downstream_but_reuses_unchanged_prefix():
    planner = WorkflowIncrementalPlanner()
    original = _workflow([_node(node_id) for node_id in "abcd"], [_edge("a", "b"), _edge("b", "c"), _edge("c", "d")])
    baseline_run, baseline_nodes = _baseline(planner, original)
    changed = deepcopy(original)
    changed["definition"]["nodes"][2]["config"]["script_key"] = "changed-script"

    plan = _plan(planner, changed, baseline_run, baseline_nodes)

    assert [(node.node_id, node.action) for node in plan.nodes] == [
        ("a", "reuse"), ("b", "reuse"), ("c", "execute"), ("d", "execute"),
    ]
    assert plan.nodes[2].reason == "node_fingerprint_changed"
    assert plan.nodes[3].reason == "upstream_execute"


@pytest.mark.parametrize("changed_id, expected", [("b", {"b", "c", "d"}), ("d", {"d"})])
def test_node_change_propagates_only_to_downstream(changed_id, expected):
    planner = WorkflowIncrementalPlanner()
    original = _workflow([_node(node_id) for node_id in "abcd"], [_edge("a", "b"), _edge("b", "c"), _edge("c", "d")])
    baseline_run, baseline_nodes = _baseline(planner, original)
    changed = deepcopy(original)
    next(node for node in changed["definition"]["nodes"] if node["id"] == changed_id)["config"]["params"] = {"x": 1}

    plan = _plan(planner, changed, baseline_run, baseline_nodes)

    assert set(plan.affected_node_ids) == expected


def test_type_edge_condition_added_and_removed_nodes_are_affected():
    planner = WorkflowIncrementalPlanner()
    original = _workflow([_node("a"), _node("b"), _node("c")], [_edge("a", "b"), _edge("b", "c")])
    baseline_run, baseline_nodes = _baseline(planner, original)
    changed = _workflow(
        [_node("a"), _node("b", node_type="agent", config={"prompt": "x", "backend_key": "b"}), _node("d")],
        [_edge("a", "b", condition={"field": "ok", "operator": "equals", "value": True}), _edge("b", "d")],
    )

    plan = _plan(planner, changed, baseline_run, baseline_nodes, runtime={"a": "r1", "b": "r1", "d": "r1"})

    # d 是新增节点，仍须执行；但因位于条件分支下，执行时是否真正到达由运行时决定。
    assert set(plan.affected_node_ids) == {"b", "d"}
    assert {node.node_id for node in plan.nodes if node.action == "execute"} == {"b", "d"}
    assert next(node for node in plan.nodes if node.node_id == "d").runtime_deferred is True


def test_skipped_conditional_branch_defers_merge_reuse_until_runtime():
    planner = WorkflowIncrementalPlanner()
    workflow = _workflow(
        [_node(node_id) for node_id in ("a", "b", "x", "merge")],
        [
            _edge("a", "b", condition={"field": "nodes.a.output.route", "operator": "equals", "value": "primary"}),
            _edge("a", "x", condition={"field": "nodes.a.output.route", "operator": "equals", "value": "secondary"}),
            _edge("b", "merge"),
            _edge("x", "merge"),
        ],
    )
    baseline_run, baseline_nodes = _baseline(planner, workflow)
    next(node for node in baseline_nodes if node["node_id"] == "x")["status"] = "skipped"

    plan = _plan(planner, workflow, baseline_run, baseline_nodes)
    by_id = {node.node_id: node for node in plan.nodes}

    assert by_id["x"].action == "execute"
    assert by_id["x"].runtime_deferred is True
    assert by_id["merge"].action == "reuse"
    assert by_id["merge"].runtime_deferred is True


def test_position_and_display_metadata_changes_do_not_change_reuse():
    planner = WorkflowIncrementalPlanner()
    original = _workflow([_node("a"), _node("b")], [_edge("a", "b")])
    baseline_run, baseline_nodes = _baseline(planner, original)
    moved = deepcopy(original)
    moved["definition"]["nodes"][0]["position"] = {"x": 999, "y": 777}
    moved["definition"]["nodes"][0]["metadata"] = {"color": "purple"}

    plan = _plan(planner, moved, baseline_run, baseline_nodes)

    assert {node.node_id for node in plan.nodes if node.action == "reuse"} == {"a", "b"}


@pytest.mark.parametrize("node_type, config", [
    ("agent", {"prompt": "x", "backend_key": "claude", "timeout_seconds": 600}),
    ("output", {"format": "markdown", "title": "Report", "path": "report.md", "prompt": "x", "backend_key": "claude", "timeout_seconds": 600}),
])
def test_agent_timeout_change_does_not_invalidate_incremental_reuse(node_type, config):
    planner = WorkflowIncrementalPlanner()
    original = _workflow([_node("a", node_type=node_type, config=config)])
    baseline_run, baseline_nodes = _baseline(planner, original)
    changed = deepcopy(original)
    changed["definition"]["nodes"][0]["config"]["timeout_seconds"] = 1800

    plan = _plan(planner, changed, baseline_run, baseline_nodes)

    assert [(node.node_id, node.action, node.reason) for node in plan.nodes] == [
        ("a", "reuse", "fingerprint_match"),
    ]


def test_output_display_title_change_does_not_invalidate_incremental_reuse():
    planner = WorkflowIncrementalPlanner()
    original = _workflow([_node("report", node_type="output", config={
        "format": "markdown", "title": "旧标题", "path": "report.md", "prompt": "x", "backend_key": "claude",
    })])
    baseline_run, baseline_nodes = _baseline(planner, original)
    changed = deepcopy(original)
    changed["definition"]["nodes"][0]["config"]["title"] = "新标题"

    plan = _plan(planner, changed, baseline_run, baseline_nodes)

    assert [(node.node_id, node.action, node.reason) for node in plan.nodes] == [
        ("report", "reuse", "fingerprint_match"),
    ]


def test_task_version_change_force_full_and_missing_baseline_execute_all():
    planner = WorkflowIncrementalPlanner()
    workflow = _workflow([_node("a"), _node("b")], [_edge("a", "b")])
    baseline_run, baseline_nodes = _baseline(planner, workflow)

    changed_task = _plan(planner, workflow, baseline_run, baseline_nodes, task=_task("v2"))
    forced = _plan(planner, workflow, baseline_run, baseline_nodes, mode="force_full")
    no_baseline = _plan(planner, workflow, None, [])

    assert {node.reason for node in changed_task.nodes} == {"no_usable_baseline"}
    assert {node.reason for node in forced.nodes} == {"force_full"}
    assert {node.reason for node in no_baseline.nodes} == {"no_usable_baseline"}


@pytest.mark.parametrize(
    "mutation, reason",
    [
        (lambda node: node.update(status="failed"), "baseline_node_not_completed"),
        (lambda node: node.pop("output"), "baseline_output_missing"),
        (lambda node: node.update(artifact_ids=["artifact-1"]), "artifact_missing"),
    ],
)
def test_unusable_baseline_node_executes_from_that_node(mutation, reason):
    planner = WorkflowIncrementalPlanner()
    workflow = _workflow([_node("a"), _node("b")], [_edge("a", "b")])
    baseline_run, baseline_nodes = _baseline(planner, workflow)
    mutation(baseline_nodes[0])

    plan = _plan(planner, workflow, baseline_run, baseline_nodes)

    assert [(node.node_id, node.action, node.reason) for node in plan.nodes] == [
        ("a", "execute", reason), ("b", "execute", "upstream_execute"),
    ]


def test_get_task_is_always_executed_to_refresh_the_current_run_lease():
    planner = WorkflowIncrementalPlanner()
    workflow = _workflow(
        [_node("task", node_type="get_task"), _node("work")],
        [_edge("task", "work")],
    )
    baseline_run, baseline_nodes = _baseline(planner, workflow)
    next(node for node in baseline_nodes if node["node_id"] == "task").update(
        output={"task": _task()}
    )

    plan = _plan(planner, workflow, baseline_run, baseline_nodes)

    assert [(node.node_id, node.action, node.reason) for node in plan.nodes] == [
        ("task", "execute", "task_lease_must_refresh"),
        ("work", "reuse", "fingerprint_match"),
    ]
    assert set(plan.affected_node_ids) == {"task"}


def test_changed_get_task_business_input_invalidates_downstream():
    planner = WorkflowIncrementalPlanner()
    workflow = _workflow(
        [_node("task", node_type="get_task"), _node("work")],
        [_edge("task", "work")],
    )
    baseline_run, baseline_nodes = _baseline(planner, workflow)
    next(node for node in baseline_nodes if node["node_id"] == "task").update(
        output={"task": {**_task(), "payload": {"version": 1}}}
    )

    plan = _plan(
        planner,
        workflow,
        baseline_run,
        baseline_nodes,
        task={**_task(), "payload": {"version": 2}},
    )

    assert [(node.node_id, node.action, node.reason) for node in plan.nodes] == [
        ("task", "execute", "task_lease_must_refresh"),
        ("work", "execute", "upstream_execute"),
    ]
    assert set(plan.affected_node_ids) == {"task", "work"}


def test_invalid_baseline_output_propagates_downstream_without_node_order_dependency():
    planner = WorkflowIncrementalPlanner()
    workflow = _workflow(
        [_node("work"), _node("task")],
        [_edge("task", "work")],
    )
    baseline_run, baseline_nodes = _baseline(planner, workflow)
    next(node for node in baseline_nodes if node["node_id"] == "task").pop("output")

    plan = _plan(planner, workflow, baseline_run, baseline_nodes)

    assert [(node.node_id, node.action, node.reason) for node in plan.nodes] == [
        ("work", "execute", "upstream_execute"),
        ("task", "execute", "baseline_output_missing"),
    ]


def test_selects_one_newest_completed_compatible_baseline_without_cross_run_nodes():
    planner = WorkflowIncrementalPlanner()
    workflow = _workflow([_node("a")])
    older, older_nodes = _baseline(planner, workflow, run_id="old")
    newest, newest_nodes = _baseline(planner, workflow, run_id="new")
    newest["finished_at"] = "2026-01-02T00:00:00+00:00"
    newest_nodes[0]["output"] = {"node": "new"}

    plan = _plan(planner, workflow, [older, newest], older_nodes + newest_nodes)

    assert plan.baseline_run_id == "new"
    assert plan.nodes[0].source_run_id == "new"
    assert plan.nodes[0].output_json == {"node": "new"}


def test_mcp_node_without_stable_runtime_fingerprint_is_not_reused():
    planner = WorkflowIncrementalPlanner()
    workflow = _workflow([_node("a", node_type="agent", config={"prompt": "x", "backend_key": "b", "mcp_enabled": True})])
    baseline_run, baseline_nodes = _baseline(planner, workflow, runtime={"a": "r1"})

    plan = _plan(planner, workflow, baseline_run, baseline_nodes, runtime={"a": {"stable": False}})

    assert plan.nodes[0].action == "execute"
    assert plan.nodes[0].reason == "resource_fingerprint_unavailable"


def test_legacy_mcp_fingerprint_reuses_unchanged_prefix_when_only_final_backend_changes():
    """旧 run 曾因 MCP 资源未知而未记录后端版本，仍应可迁移复用。"""
    planner = WorkflowIncrementalPlanner()
    original = _workflow(
        [
            _node(
                "enrich",
                node_type="agent",
                config={"prompt": "collect", "backend_key": "opencode", "mcp_enabled": True},
            ),
            _node(
                "markdown",
                node_type="output",
                config={"format": "markdown", "title": "Report", "path": "report.md", "prompt": "write", "backend_key": "claude"},
            ),
            _node(
                "html",
                node_type="output",
                config={"format": "html", "title": "Report", "path": "report.html", "prompt": "render", "backend_key": "codex"},
            ),
        ],
        [_edge("enrich", "markdown"), _edge("markdown", "html")],
    )
    baseline_run, baseline_nodes = _baseline(
        planner,
        original,
        runtime={"enrich": None, "markdown": "claude-v1", "html": "codex-v1"},
    )
    changed = deepcopy(original)
    changed["definition"]["nodes"][2]["config"]["backend_key"] = "pi"

    plan = _plan(
        planner,
        changed,
        baseline_run,
        baseline_nodes,
        runtime={"enrich": "opencode-v1", "markdown": "claude-v1", "html": "pi-v1"},
    )

    assert [(node.node_id, node.action, node.reason) for node in plan.nodes] == [
        ("enrich", "reuse", "legacy_mcp_fingerprint_match"),
        ("markdown", "reuse", "fingerprint_match"),
        ("html", "execute", "node_fingerprint_changed"),
    ]
