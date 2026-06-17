from __future__ import annotations

import time
from datetime import datetime, timedelta


def _create_workflow(store, key: str, profile_key: str = "report-plane"):
    store.upsert_workflow_definition(
        workflow_key=key,
        name=key,
        description="",
        profile_key=profile_key,
        workflow_js="",
        manifest={"name": key, "nodes": [], "edges": [], "schemas": {}},
        status="active",
        created_by="root",
    )


def _wait_runs_done(scheduler, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not scheduler._running:
            return
        time.sleep(0.02)


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


def test_window_anchor_handles_cross_midnight_and_same_day_windows(wm_paths):
    from agent_bridge.knowledge.service import AgentBridgeService
    from agent_bridge.workflows.scheduler import WorkflowScheduler

    svc = AgentBridgeService.create(wm_paths, {"root"})
    scheduler = WorkflowScheduler(service=svc.workflows, store=svc.store, admins={"root"})

    # Overnight window 22:00 -> 07:00.
    scheduler._start_time = datetime.strptime("22:00", "%H:%M").time()
    scheduler._stop_time = datetime.strptime("07:00", "%H:%M").time()

    assert scheduler._window_anchor(datetime(2026, 6, 16, 23, 30)) is not None  # before midnight
    assert scheduler._window_anchor(datetime(2026, 6, 17, 6, 30)) is not None   # after midnight
    assert scheduler._window_anchor(datetime(2026, 6, 17, 12, 0)) is None       # outside

    # The post-midnight leg belongs to the window that opened the previous day.
    assert scheduler._window_anchor(datetime(2026, 6, 17, 6, 30)) == datetime(2026, 6, 16).date()
    assert scheduler._window_anchor(datetime(2026, 6, 16, 23, 30)) == datetime(2026, 6, 16).date()

    # Same-day window 09:00 -> 18:00.
    scheduler._start_time = datetime.strptime("09:00", "%H:%M").time()
    scheduler._stop_time = datetime.strptime("18:00", "%H:%M").time()
    assert scheduler._window_anchor(datetime(2026, 6, 16, 12, 0)) == datetime(2026, 6, 16).date()
    assert scheduler._window_anchor(datetime(2026, 6, 16, 8, 59)) is None
    assert scheduler._window_anchor(datetime(2026, 6, 16, 18, 0)) is None

    # Always-on (blank window): open every moment, anchored to the calendar date.
    scheduler._start_time = None
    scheduler._stop_time = None
    assert scheduler._window_anchor(datetime(2026, 6, 16, 3, 0)) == datetime(2026, 6, 16).date()


def test_scheduler_reads_workflow_window_from_system_config(wm_paths):
    from agent_bridge.knowledge.service import AgentBridgeService
    from agent_bridge.workflows.runner import FakeWorkflowRunner
    from agent_bridge.workflows.scheduler import WorkflowScheduler

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.save_sync_config(
        code_sync_cron="0 * * * *",
        understand_cron="0 2 * * *",
        doc_sync_cron="*/30 * * * *",
        workflow_start_time="23:15",
        workflow_stop_time="06:00",
    )
    scheduler = WorkflowScheduler(service=svc.workflows, store=svc.store, admins={"root"}, runner=FakeWorkflowRunner())

    scheduler.start()
    try:
        status = scheduler.get_status()
    finally:
        scheduler.stop()

    assert status["start_time"] == "23:15"
    assert status["stop_time"] == "06:00"
    assert [job["repo_key"] for job in status["jobs"]] == ["workflow_tick"]


def test_scheduler_tick_runs_inside_window_and_skips_outside(wm_paths, tmp_path):
    from agent_bridge.knowledge.service import AgentBridgeService
    from agent_bridge.workflows.runner import FakeWorkflowRunner
    from agent_bridge.workflows.scheduler import WorkflowScheduler

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    _create_workflow(svc.store, "A")

    now = datetime.now()
    in_window = WorkflowScheduler(
        service=svc.workflows,
        store=svc.store,
        admins={"root"},
        runner=FakeWorkflowRunner(status="no_executable_task"),
        base_run_dir=tmp_path,
        max_concurrent_workflows=2,
    )
    in_window._start_time = (now - timedelta(minutes=1)).time().replace(second=0, microsecond=0)
    in_window._stop_time = (now + timedelta(minutes=1)).time().replace(second=0, microsecond=0)

    in_window.tick()
    _wait_runs_done(in_window)
    assert "A" in in_window.finished_today

    out_window = WorkflowScheduler(
        service=svc.workflows,
        store=svc.store,
        admins={"root"},
        runner=FakeWorkflowRunner(status="no_executable_task"),
        base_run_dir=tmp_path,
        max_concurrent_workflows=2,
    )
    out_window._start_time = (now + timedelta(hours=1)).time().replace(second=0, microsecond=0)
    out_window._stop_time = (now + timedelta(hours=2)).time().replace(second=0, microsecond=0)

    out_window.tick()
    assert not out_window._running
    assert "A" not in out_window.finished_today


def test_failed_run_releases_leased_task_for_retry(wm_paths, tmp_path):
    from agent_bridge.knowledge.service import AgentBridgeService
    from agent_bridge.workflows.runner import WorkflowProcessResult, prepare_run_directory
    from agent_bridge.workflows.scheduler import WorkflowScheduler

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    _create_workflow(svc.store, "A")
    svc.store.upsert_workflow_tasks("A", [{"task_key": "page:a", "payload": {}}])

    class _LeasingFailingRunner:
        def run(self, base_dir, spec):
            run_dir = prepare_run_directory(base_dir, spec)
            # Simulate the agent leasing a task, then the claude run failing.
            svc.store.lease_workflow_task(spec.workflow_key, run_id=spec.run_id, lease_seconds=7200)
            return WorkflowProcessResult(
                run_dir=run_dir,
                exit_code=1,
                stdout_path=run_dir / "stdout.log",
                stderr_path=run_dir / "stderr.log",
                duration_ms=1,
            )

    scheduler = WorkflowScheduler(
        service=svc.workflows,
        store=svc.store,
        admins={"root"},
        runner=_LeasingFailingRunner(),
        base_run_dir=tmp_path,
        max_concurrent_workflows=2,
    )
    result = scheduler.run_one_workflow("A")

    assert result["status"] == "failed"
    task = svc.store.get_workflow_task("A", "page:a")
    assert task["status"] == "pending"  # released for fast retry instead of waiting out the lease
    assert task["lease_run_id"] is None
    assert task["last_error"]


def test_run_workflow_now_runs_once_and_creates_run_row(wm_paths, tmp_path):
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

    result = scheduler.run_workflow_now("A")
    assert result["status"] == "started"
    assert result["run_id"].startswith("run_")

    _wait_runs_done(scheduler)
    run = svc.store.get_workflow_run(result["run_id"])
    assert run is not None
    assert run["status"] == "no_task"


def test_run_workflow_now_rejects_when_already_running(wm_paths, tmp_path):
    import pytest
    from agent_bridge.core.domain import ConflictError
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
        runner=FakeWorkflowRunner(),
        base_run_dir=tmp_path,
    )
    scheduler._running.add("A")  # simulate an in-flight run

    with pytest.raises(ConflictError):
        scheduler.run_workflow_now("A")


def test_run_workflow_now_missing_workflow_raises_not_found(wm_paths, tmp_path):
    import pytest
    from agent_bridge.core.domain import NotFound
    from agent_bridge.knowledge.service import AgentBridgeService
    from agent_bridge.workflows.scheduler import WorkflowScheduler

    svc = AgentBridgeService.create(wm_paths, {"root"})
    scheduler = WorkflowScheduler(
        service=svc.workflows, store=svc.store, admins={"root"}, base_run_dir=tmp_path,
    )

    with pytest.raises(NotFound):
        scheduler.run_workflow_now("does-not-exist")


def test_run_workflow_now_bypasses_disabled_status(wm_paths, tmp_path):
    from agent_bridge.knowledge.service import AgentBridgeService
    from agent_bridge.workflows.runner import FakeWorkflowRunner
    from agent_bridge.workflows.scheduler import WorkflowScheduler

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    svc.store.upsert_workflow_definition(
        workflow_key="A", name="A", description="", profile_key="report-plane",
        workflow_js="", manifest={"name": "A", "nodes": [], "edges": [], "schemas": {}},
        status="disabled", created_by="root",
    )

    scheduler = WorkflowScheduler(
        service=svc.workflows, store=svc.store, admins={"root"},
        runner=FakeWorkflowRunner(status="no_executable_task"), base_run_dir=tmp_path,
    )

    result = scheduler.run_workflow_now("A")  # disabled, yet runnable for a test
    assert result["status"] == "started"
