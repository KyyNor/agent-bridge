from __future__ import annotations

from datetime import datetime


def _create_workflow(store, key: str, profile_key: str = "report-plane", schedule: dict | None = None):
    store.upsert_workflow_definition(
        workflow_key=key,
        name=key,
        description="",
        profile_key=profile_key,
        workflow_js="",
        manifest={"name": key, "nodes": [], "edges": [], "schemas": {}},
        schedule=schedule or {"enabled": True, "start_time": "22:00", "stop_time": "07:00"},
        status="active",
        created_by="root",
    )


def test_scheduler_selects_different_workflows_with_round_robin(wm_paths):
    from agent_bridge.knowledge.service import AgentBridgeService
    from agent_bridge.workflows.runner import FakeWorkflowRunner
    from agent_bridge.workflows.scheduler import WorkflowScheduler

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    for key in ["A", "B", "C", "D"]:
        _create_workflow(svc.store, key)

    scheduler = WorkflowScheduler(
        service=svc.workflows,
        store=svc.store,
        admins={"root"},
        runner=FakeWorkflowRunner(),
        max_concurrent_workflows=2,
    )

    first = scheduler.next_workflow_batch({"A", "B", "C", "D"}, running=set())
    second = scheduler.next_workflow_batch({"A", "B", "C", "D"}, running=set(first))

    assert first == ["A", "B"]
    assert second == ["C", "D"]


def test_scheduler_marks_no_task_workflow_finished_for_day(wm_paths, tmp_path):
    from agent_bridge.knowledge.service import AgentBridgeService
    from agent_bridge.workflows.runner import FakeWorkflowRunner
    from agent_bridge.workflows.scheduler import WorkflowScheduler

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    _create_workflow(svc.store, "A")

    scheduler = WorkflowScheduler(
        service=svc.workflows,
        store=svc.store,
        admins={"root"},
        runner=FakeWorkflowRunner(status="no_executable_task"),
        base_run_dir=tmp_path,
        max_concurrent_workflows=2,
    )

    result = scheduler.run_one_workflow("A")

    assert result["status"] == "no_task"
    assert "A" in scheduler.finished_today


def test_scheduler_respects_cross_midnight_schedule_window(wm_paths):
    from agent_bridge.knowledge.service import AgentBridgeService
    from agent_bridge.workflows.runner import FakeWorkflowRunner
    from agent_bridge.workflows.scheduler import WorkflowScheduler

    svc = AgentBridgeService.create(wm_paths, {"root"})
    scheduler = WorkflowScheduler(service=svc.workflows, store=svc.store, admins={"root"}, runner=FakeWorkflowRunner())
    schedule = {"enabled": True, "start_time": "22:00", "stop_time": "07:00"}

    assert scheduler.schedule_allows_start(schedule, now=datetime(2026, 6, 16, 23, 30))
    assert scheduler.schedule_allows_start(schedule, now=datetime(2026, 6, 17, 6, 30))
    assert not scheduler.schedule_allows_start(schedule, now=datetime(2026, 6, 17, 12, 0))


def test_scheduler_uses_shared_workflow_cron_from_system_config(wm_paths):
    from agent_bridge.knowledge.service import AgentBridgeService
    from agent_bridge.workflows.runner import FakeWorkflowRunner
    from agent_bridge.workflows.scheduler import WorkflowScheduler

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.save_sync_config(
        code_sync_cron="0 * * * *",
        understand_cron="0 2 * * *",
        doc_sync_cron="*/30 * * * *",
        workflow_cron="15 23 * * *",
    )
    scheduler = WorkflowScheduler(service=svc.workflows, store=svc.store, admins={"root"}, runner=FakeWorkflowRunner())

    scheduler.start()
    try:
        status = scheduler.get_status()
    finally:
        scheduler.stop()

    assert status["cron"] == "15 23 * * *"
    assert [job["repo_key"] for job in status["jobs"]] == ["workflow_tick"]
