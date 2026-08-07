from __future__ import annotations

from datetime import datetime, timezone


def _seed_workflow(store, workflow_key: str = "workflow-recovery") -> None:
    store.upsert_project_profile(
        profile_key="workflow-profile",
        name="Workflow Profile",
        created_by="root",
    )
    store.upsert_workflow_definition(
        workflow_key=workflow_key,
        name="Workflow",
        description="",
        profile_key="workflow-profile",
        definition={"nodes": [], "edges": []},
        status="active",
        created_by="root",
    )


def test_recover_interrupted_workflow_runs_closes_nodes_and_releases_leases(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    _seed_workflow(store)
    store.upsert_workflow_tasks(
        "workflow-recovery",
        [{"task_key": "task-1", "task_version": "v1", "payload": {}}],
    )
    store.create_workflow_run(
        run_id="workflow-run-1",
        workflow_key="workflow-recovery",
        profile_key="workflow-profile",
        task_key="task-1",
        task_version="v1",
        status="running",
        temp_dir="",
    )
    store.create_workflow_node_runs(
        "workflow-run-1",
        [{"node_id": "agent", "node_type": "agent"}],
    )
    store.start_workflow_node_run("workflow-run-1", "agent")
    leased = store.lease_workflow_task_by_key(
        "workflow-recovery",
        "task-1",
        task_version="v1",
        run_id="workflow-run-1",
        lease_seconds=7200,
    )
    assert leased is not None
    store.agent_runs.create(
        run_key="agent-run-1",
        agent_name="workflow-agent",
        workflow_key="workflow-recovery",
        workflow_run_id="workflow-run-1",
        prompt="run",
        started_at="2026-08-07T03:00:00+00:00",
    )

    recovered = store.recover_interrupted_workflow_runs()
    recovered_agents = store.agent_runs.recover_interrupted_workflow_runs(
        recovered["run_ids"]
    )

    assert recovered == {
        "run_ids": ["workflow-run-1"],
        "runs": 1,
        "nodes": 1,
        "tasks": 1,
    }
    assert recovered_agents == 1
    assert store.get_workflow_run("workflow-run-1")["status"] == "failed"
    assert store.list_workflow_node_runs("workflow-run-1")[0]["status"] == "failed"
    assert store.get_workflow_task("workflow-recovery", "task-1", task_version="v1")["status"] == "pending"
    assert store.agent_runs.get("agent-run-1")["status"] == "failed"
    assert store.recover_interrupted_workflow_runs()["runs"] == 0


def test_scheduler_restores_scheduled_window_state(wm_paths, monkeypatch):
    from agent_bridge.automation.workflows.scheduler import WorkflowScheduler
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    _seed_workflow(store, "scheduled-workflow")
    store.save_sync_config(
        code_sync_cron="",
        workflow_start_time="00:00",
        workflow_stop_time="23:59",
        workflow_max_runs=2,
    )
    store.create_workflow_run(
        run_id="scheduled-run-1",
        workflow_key="scheduled-workflow",
        profile_key="workflow-profile",
        task_key=None,
        status="completed",
        temp_dir="",
        definition_snapshot={"nodes": [], "edges": []},
        execution_plan={"trigger": "scheduled"},
    )
    store.create_workflow_run(
        run_id="manual-run-1",
        workflow_key="scheduled-workflow",
        profile_key="workflow-profile",
        task_key=None,
        status="completed",
        temp_dir="",
        definition_snapshot={"nodes": [], "edges": []},
        execution_plan={},
    )
    with store.connect() as conn:
        conn.execute(
            "UPDATE workflow_runs SET started_at = ? WHERE run_id IN (?, ?)",
            ("2026-08-07 03:00:00", "scheduled-run-1", "manual-run-1"),
        )

    fixed_now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "agent_bridge.automation.workflows.scheduler.local_now",
        lambda: fixed_now,
    )
    scheduler = WorkflowScheduler(service=object(), store=store, admins={"root"})
    scheduler._load_window()
    scheduler._restore_window_state()

    assert scheduler.run_counts == {"scheduled-workflow": 1}
    assert scheduler.finished_today == {"scheduled-workflow"}
