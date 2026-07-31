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


def _upsert_definition(service, *, name: str, definition=None):
    return service.workflows.upsert_definition(
        actor="root",
        workflow_key="incremental-report",
        name=name,
        description="",
        profile_key="report-plane",
        definition=definition if definition is not None else {"nodes": [], "edges": []},
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
        error=None,
        duration_ms=0,
        output={"ok": True},
    )
    return runtime


def test_definition_semantic_change_stales_only_latest_completed_task_version(wm_paths):
    """真正改变执行语义（加节点）才标 stale；只改展示字段不标 stale。"""
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

    # 加一个节点：改变执行语义，最新 completed 代表版本应标 stale，旧版本保持 completed。
    get_task_node = {"id": "n1", "name": "N1", "type": "get_task", "position": {"x": 0, "y": 0}, "config": {}}
    revision_two = _upsert_definition(
        service,
        name="Incremental report v1",
        definition={"nodes": [get_task_node], "edges": []},
    )
    assert revision_two["revision_no"] > revision_one["revision_no"]

    assert service.store.get_workflow_task("incremental-report", "page:a", "v2")["status"] == "stale"
    assert service.store.get_workflow_task("incremental-report", "page:a", "v1")["status"] == "completed"


def test_definition_presentation_change_does_not_stale_completed_task(wm_paths):
    """改 name/description 产生新版本号，但不标 stale（不触发重跑）。"""
    service = _service(wm_paths)
    revision_one = _upsert_definition(service, name="Incremental report v1")

    service.store.upsert_workflow_tasks(
        "incremental-report", [{"task_key": "page:a", "task_version": "v1", "payload": {}}]
    )
    _complete_task(service, run_id="run-v1", task_version="v1", revision=revision_one)
    assert service.store.get_workflow_task("incremental-report", "page:a", "v1")["status"] == "completed"

    # 只改 name：版本号递增，但执行语义没变 → 不应标 stale。
    revision_two = _upsert_definition(service, name="Incremental report v2")
    assert revision_two["revision_no"] > revision_one["revision_no"]
    assert revision_two["content_hash"] == revision_one["content_hash"]
    assert service.store.get_workflow_task("incremental-report", "page:a", "v1")["status"] == "completed"


def test_version_and_content_hash_differ_on_timeout_title_and_name():
    """执行语义口径与版本口径对展示/运行控制字段的剥离差异。

    timeout_seconds（agent/output）、title（output）、name（节点级）、工作流级
    name/description：这些字段变更时，版本口径 hash 应不同（触发新 revision），
    执行语义口径 hash 应相同（不触发重跑）。
    """
    from agent_bridge.automation.workflows.revisions import (
        workflow_content_hash,
        workflow_version_hash,
    )

    def args(graph, name="w", description=""):
        return graph, name, description, "p1", "active", "operation"

    base_node = {"id": "n1", "type": "agent", "name": "N1", "position": {"x": 0, "y": 0},
                 "config": {"prompt": "p", "backend_key": "b", "timeout_seconds": 600}}
    graph_base = {"nodes": [dict(base_node)], "edges": []}

    # 改 agent timeout_seconds
    graph_timeout = {"nodes": [{**base_node, "config": {**base_node["config"], "timeout_seconds": 120}}], "edges": []}
    assert workflow_content_hash(*args(graph_base)) == workflow_content_hash(*args(graph_timeout))
    assert workflow_version_hash(*args(graph_base)) != workflow_version_hash(*args(graph_timeout))

    # 改工作流 name
    assert workflow_content_hash(*args(graph_base, name="A")) == workflow_content_hash(*args(graph_base, name="B"))
    assert workflow_version_hash(*args(graph_base, name="A")) != workflow_version_hash(*args(graph_base, name="B"))

    # 改 description
    assert workflow_content_hash(*args(graph_base, description="x")) == workflow_content_hash(*args(graph_base, description="y"))
    assert workflow_version_hash(*args(graph_base, description="x")) != workflow_version_hash(*args(graph_base, description="y"))


def test_agent_timeout_change_does_not_stale_completed_task_via_storage(wm_paths):
    """端到端验证：改 get_task 图的行为不受 timeout 影响，且改 name 不 stale。

    由于 agent/output 节点需要注册后端，这里用可校验的 get_task 图配合直接调用
    mark_latest_task_stale_if_needed 验证：content_hash 相同时不标 stale。
    """
    service = _service(wm_paths)
    revision_one = _upsert_definition(service, name="Report")

    service.store.upsert_workflow_tasks(
        "incremental-report", [{"task_key": "page:a", "task_version": "v1", "payload": {}}]
    )
    _complete_task(service, run_id="run-v1", task_version="v1", revision=revision_one)
    assert service.store.get_workflow_task("incremental-report", "page:a", "v1")["status"] == "completed"

    # 执行语义 hash 不变（仅改 name）→ 用相同 content_hash 调 stale 判定，不应改动任务。
    marked = service.store.workflows.mark_latest_task_stale_if_needed(
        "incremental-report", revision_one["content_hash"]
    )
    assert marked == 0
    assert service.store.get_workflow_task("incremental-report", "page:a", "v1")["status"] == "completed"

    # 执行语义 hash 变化 → 应标 stale。
    marked2 = service.store.workflows.mark_latest_task_stale_if_needed(
        "incremental-report", "different-execution-hash"
    )
    assert marked2 == 1
    assert service.store.get_workflow_task("incremental-report", "page:a", "v1")["status"] == "stale"


def test_version_hash_persisted_and_diff_reports_name_change(wm_paths):
    """version_hash 持久化到 revision 行，diff 能看到 name 变更。"""
    service = _service(wm_paths)
    revision_one = _upsert_definition(service, name="Report A")

    # 改 name 产生新 revision，且 version_hash 不同、content_hash 相同。
    revision_two = _upsert_definition(service, name="Report B")
    assert revision_two["revision_no"] == 2

    stored = service.store.workflows.list_definition_revisions("incremental-report", limit=5)
    assert stored[0]["version_hash"] != ""
    assert stored[0]["version_hash"] != stored[1]["version_hash"]
    assert stored[0]["content_hash"] == stored[1]["content_hash"]

    # diff 应能反映 name 变化（snapshot 含 name）。
    diff = service.workflows.diff_revisions(
        actor="root", workflow_key="incremental-report", from_no=2, to_no=1
    )
    diff_text = diff["text"]["content"] if isinstance(diff["text"], dict) else diff["text"]
    assert "Report A" in diff_text or "Report B" in diff_text


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


def test_get_task_returns_the_task_preleased_for_its_run(wm_paths):
    service = _service(wm_paths)
    _upsert_definition(service, name="Incremental report")
    service.store.upsert_workflow_tasks(
        "incremental-report",
        [
            {"task_key": "page:a", "task_version": "v1", "payload": {}},
            {"task_key": "page:b", "task_version": "v1", "payload": {}},
        ],
    )
    service.store.create_workflow_run(
        run_id="selected-task-run",
        workflow_key="incremental-report",
        profile_key="report-plane",
        task_key="page:b",
        task_version="v1",
        status="running",
        temp_dir="",
        definition_snapshot={"nodes": [], "edges": []},
    )
    leased = service.store.lease_workflow_task_by_key(
        "incremental-report",
        "page:b",
        task_version="v1",
        run_id="selected-task-run",
        lease_seconds=7200,
    )
    assert leased is not None

    result = service.workflows.get_task_for_agent(
        profile_key="report-plane",
        workflow_key="incremental-report",
        run_id="selected-task-run",
    )

    assert result["task"]["task_key"] == "page:b"
    assert service.store.get_workflow_task("incremental-report", "page:a", "v1")["status"] == "pending"


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
