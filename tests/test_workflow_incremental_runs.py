from __future__ import annotations

from agent_bridge.automation.workflows.incremental import WorkflowIncrementalPlanner


def _service(wm_paths):
    from agent_bridge.app.service import AgentBridgeService

    service = AgentBridgeService.create(wm_paths, {"root"})
    service.store.init_schema()
    service.store.upsert_project_profile(
        profile_key="report-plane", name="Report Plane", created_by="root"
    )
    return service


def _upsert_definition(service, *, name: str):
    return service.workflows.upsert_definition(
        actor="root",
        workflow_key="incremental-report",
        name=name,
        description="",
        profile_key="report-plane",
        definition={"nodes": [], "edges": []},
        status="active",
    )


def _complete_task(service, *, run_id: str, task_version: str, revision: dict):
    service.store.create_workflow_run(
        run_id=run_id,
        workflow_key="incremental-report",
        profile_key="report-plane",
        task_key=None,
        status="running",
        temp_dir="",
        definition_snapshot={"nodes": [], "edges": []},
        workflow_revision_no=revision["revision_no"],
        workflow_content_hash=revision["content_hash"],
        task_version=task_version,
        execution_mode="incremental",
    )
    task = service.store.lease_workflow_task(
        "incremental-report", run_id=run_id, lease_seconds=7200
    )
    assert task is not None
    assert task["task_version"] == task_version
    assert service.store.complete_workflow_task(
        "incremental-report", task["task_key"], task_version=task_version, run_id=run_id
    )
    service.store.finish_workflow_run(
        run_id,
        status="completed",
        exit_code=0,
        stdout_path=None,
        stderr_path=None,
        error=None,
        duration_ms=0,
        output={"ok": True},
    )


def _script_graph(node_ids: list[str]) -> dict:
    nodes = [
        {
            "id": node_id,
            "type": "script",
            "name": node_id,
            "position": {"x": index, "y": 0},
            "config": {
                "script_key": f"script.{node_id}",
                "params": {},
                "timeout_seconds": 60,
            },
        }
        for index, node_id in enumerate(node_ids)
    ]
    return {
        "nodes": nodes,
        "edges": [
            {
                "id": f"{source}-{target}",
                "source": source,
                "target": target,
            }
            for source, target in zip(node_ids, node_ids[1:])
        ],
    }


def _seed_completed_baseline(
    service,
    *,
    run_id: str,
    graph: dict,
    artifact_ids_by_node: dict[str, list[str]] | None = None,
):
    planner = WorkflowIncrementalPlanner()
    runtime = {node["id"]: "runtime-v1" for node in graph["nodes"]}
    service.store.create_workflow_run(
        run_id=run_id,
        workflow_key="incremental-report",
        profile_key="report-plane",
        task_key="page:a",
        status="running",
        temp_dir="",
        definition_snapshot=graph,
        task_version="v1",
        execution_mode="normal",
    )
    service.store.create_workflow_node_runs(
        run_id,
        [
            {
                "node_id": node["id"],
                "node_type": node["type"],
                "node_fingerprint": planner.node_fingerprint(
                    node, runtime_fingerprint=runtime
                ),
            }
            for node in graph["nodes"]
        ],
    )
    for node in graph["nodes"]:
        service.store.finish_workflow_node_run(
            run_id,
            node["id"],
            status="completed",
            output={"node": node["id"]},
            artifact_ids=(artifact_ids_by_node or {}).get(node["id"], []),
        )
    service.store.finish_workflow_run(
        run_id,
        status="completed",
        exit_code=0,
        stdout_path=None,
        stderr_path=None,
        error=None,
        duration_ms=0,
        output={"ok": True},
    )
    return runtime


def test_definition_change_stales_only_latest_completed_task_version(wm_paths):
    service = _service(wm_paths)
    revision_one = _upsert_definition(service, name="Incremental report v1")

    service.store.upsert_workflow_tasks(
        "incremental-report", [{"task_key": "page:a", "task_version": "v1", "payload": {}}]
    )
    _complete_task(service, run_id="run-v1", task_version="v1", revision=revision_one)

    service.store.upsert_workflow_tasks(
        "incremental-report", [{"task_key": "page:a", "task_version": "v2", "payload": {}}]
    )
    _complete_task(service, run_id="run-v2", task_version="v2", revision=revision_one)

    _upsert_definition(service, name="Incremental report v2")

    assert service.store.get_workflow_task("incremental-report", "page:a", "v2")["status"] == "stale"
    assert service.store.get_workflow_task("incremental-report", "page:a", "v1")["status"] == "completed"


def test_incremental_plan_selects_newest_compatible_completed_baseline(wm_paths):
    service = _service(wm_paths)
    revision = _upsert_definition(service, name="Incremental report")
    service.store.upsert_workflow_tasks(
        "incremental-report", [{"task_key": "page:a", "task_version": "v1", "payload": {}}]
    )
    _complete_task(service, run_id="baseline-old", task_version="v1", revision=revision)

    service.store.reset_workflow_task("incremental-report", "page:a", task_version="v1")
    _complete_task(service, run_id="baseline-new", task_version="v1", revision=revision)

    plan = service.workflows.build_incremental_plan(
        actor="root",
        workflow_key="incremental-report",
        task_key="page:a",
        task_version="v1",
        execution_mode="incremental",
    )

    assert plan.baseline_run_id == "baseline-new"


def test_incremental_plan_reuses_prefix_when_current_graph_adds_a_node(wm_paths, monkeypatch):
    service = _service(wm_paths)
    _upsert_definition(service, name="Incremental report")
    service.store.upsert_workflow_tasks(
        "incremental-report", [{"task_key": "page:a", "task_version": "v1", "payload": {}}]
    )
    baseline_graph = _script_graph(["load", "transform"])
    runtime = _seed_completed_baseline(
        service, run_id="baseline-add-node", graph=baseline_graph
    )
    current_graph = _script_graph(["load", "transform", "publish"])
    monkeypatch.setattr(
        service.workflows.validator,
        "resolve_resource_fingerprints",
        lambda *, actor, graph: {node.id: runtime[node.id] if node.id in runtime else "runtime-v1" for node in graph.nodes},
    )

    plan = service.workflows.build_incremental_plan(
        actor="root",
        workflow_key="incremental-report",
        task_key="page:a",
        task_version="v1",
        execution_mode="incremental",
        workflow={
            "workflow_key": "incremental-report",
            "profile_key": "report-plane",
            "definition": current_graph,
        },
        definition=current_graph,
    )

    assert plan.baseline_run_id == "baseline-add-node"
    assert [(node.node_id, node.action, node.reason) for node in plan.nodes] == [
        ("load", "reuse", "fingerprint_match"),
        ("transform", "reuse", "fingerprint_match"),
        ("publish", "execute", "new_node"),
    ]


def test_incremental_plan_loads_one_baseline_and_deduplicates_artifact_reads(wm_paths, monkeypatch):
    service = _service(wm_paths)
    _upsert_definition(service, name="Incremental report")
    service.store.upsert_workflow_tasks(
        "incremental-report", [{"task_key": "page:a", "task_version": "v1", "payload": {}}]
    )
    graph = _script_graph(["load"])
    _seed_completed_baseline(
        service,
        run_id="baseline-query-count",
        graph=graph,
        artifact_ids_by_node={"load": ["artifact-1", "artifact-1"]},
    )
    service.workflows.validator.resolve_resource_fingerprints = (
        lambda *, actor, graph: {node.id: "runtime-v1" for node in graph.nodes}
    )
    query = {"run_limit": None, "node_runs": 0, "artifacts": []}
    original_list_runs = service.store.workflows.list_completed_workflow_runs_for_task
    original_list_nodes = service.store.list_workflow_node_runs

    def list_runs(*args, **kwargs):
        query["run_limit"] = kwargs.get("limit")
        return original_list_runs(*args, **kwargs)

    def list_nodes(run_id):
        query["node_runs"] += 1
        return original_list_nodes(run_id)

    def get_artifact(artifact_id):
        query["artifacts"].append(artifact_id)
        return {
            "artifact_id": artifact_id,
            "workflow_key": "incremental-report",
            "profile_key": "report-plane",
            "task_key": "page:a",
            "task_version": "v1",
            "content": "artifact",
        }

    monkeypatch.setattr(
        service.store.workflows,
        "list_completed_workflow_runs_for_task",
        list_runs,
    )
    monkeypatch.setattr(service.store, "list_workflow_node_runs", list_nodes)
    monkeypatch.setattr(service.store, "get_workflow_artifact", get_artifact)

    plan = service.workflows.build_incremental_plan(
        actor="root",
        workflow_key="incremental-report",
        task_key="page:a",
        task_version="v1",
        execution_mode="incremental",
        workflow={
            "workflow_key": "incremental-report",
            "profile_key": "report-plane",
            "definition": graph,
        },
        definition=graph,
    )

    assert plan.nodes[0].action == "reuse"
    assert query == {"run_limit": 1, "node_runs": 1, "artifacts": ["artifact-1"]}
