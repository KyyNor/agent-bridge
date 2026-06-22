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
    from agent_bridge.app.service import AgentBridgeService
    from agent_bridge.automation.workflows.runner import FakeWorkflowRunner
    from agent_bridge.automation.workflows.scheduler import WorkflowScheduler

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
    from agent_bridge.app.service import AgentBridgeService
    from agent_bridge.automation.workflows.runner import FakeWorkflowRunner
    from agent_bridge.automation.workflows.scheduler import WorkflowScheduler

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
    from agent_bridge.app.service import AgentBridgeService
    from agent_bridge.automation.workflows.scheduler import WorkflowScheduler

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
    from agent_bridge.app.service import AgentBridgeService
    from agent_bridge.automation.workflows.runner import FakeWorkflowRunner
    from agent_bridge.automation.workflows.scheduler import WorkflowScheduler

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
    job_ids = {job["repo_key"] for job in status["jobs"]}
    assert "workflow_window_open" in job_ids
    assert "workflow_window_close" in job_ids
    if status["in_window"]:
        assert "workflow_tick" in job_ids
    else:
        assert "workflow_tick" not in job_ids


def test_scheduler_tick_runs_inside_window_and_skips_outside(wm_paths, tmp_path):
    from agent_bridge.app.service import AgentBridgeService
    from agent_bridge.automation.workflows.runner import FakeWorkflowRunner
    from agent_bridge.automation.workflows.scheduler import WorkflowScheduler

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


def test_refresh_jobs_skips_minute_tick_outside_window(wm_paths, tmp_path):
    from agent_bridge.app.service import AgentBridgeService
    from agent_bridge.automation.workflows.runner import FakeWorkflowRunner
    from agent_bridge.automation.workflows.scheduler import WorkflowScheduler

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    scheduler = WorkflowScheduler(
        service=svc.workflows,
        store=svc.store,
        admins={"root"},
        runner=FakeWorkflowRunner(),
        base_run_dir=tmp_path,
    )

    now = datetime.now()
    scheduler._start_time = (now + timedelta(hours=1)).time().replace(second=0, microsecond=0)
    scheduler._stop_time = (now + timedelta(hours=2)).time().replace(second=0, microsecond=0)
    scheduler._ensure_scheduler()

    scheduler._refresh_jobs()

    job_ids = {job.id for job in scheduler._scheduler.get_jobs()}
    assert "workflow_tick" not in job_ids
    assert "workflow_window_open" in job_ids
    assert "workflow_window_close" in job_ids


def test_refresh_jobs_adds_minute_tick_inside_window(wm_paths, tmp_path):
    from agent_bridge.app.service import AgentBridgeService
    from agent_bridge.automation.workflows.runner import FakeWorkflowRunner
    from agent_bridge.automation.workflows.scheduler import WorkflowScheduler

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    scheduler = WorkflowScheduler(
        service=svc.workflows,
        store=svc.store,
        admins={"root"},
        runner=FakeWorkflowRunner(),
        base_run_dir=tmp_path,
    )

    now = datetime.now()
    scheduler._start_time = (now - timedelta(minutes=1)).time().replace(second=0, microsecond=0)
    scheduler._stop_time = (now + timedelta(minutes=1)).time().replace(second=0, microsecond=0)
    scheduler._ensure_scheduler()

    scheduler._refresh_jobs()

    job_ids = {job.id for job in scheduler._scheduler.get_jobs()}
    assert "workflow_tick" in job_ids
    assert "workflow_window_open" in job_ids
    assert "workflow_window_close" in job_ids


def test_failed_run_releases_leased_task_for_retry(wm_paths, tmp_path):
    from agent_bridge.app.service import AgentBridgeService
    from agent_bridge.automation.workflows.runner import WorkflowProcessResult, prepare_run_directory
    from agent_bridge.automation.workflows.scheduler import WorkflowScheduler

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
    from agent_bridge.app.service import AgentBridgeService
    from agent_bridge.automation.workflows.runner import FakeWorkflowRunner
    from agent_bridge.automation.workflows.scheduler import WorkflowScheduler

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
    assert result["run_id"].startswith("A_")  # {workflow_key}_{uuid7hex}

    _wait_runs_done(scheduler)
    run = svc.store.get_workflow_run(result["run_id"])
    assert run is not None
    assert run["status"] == "no_task"


def test_run_workflow_now_rejects_when_already_running(wm_paths, tmp_path):
    import pytest
    from agent_bridge.core.domain import ConflictError
    from agent_bridge.app.service import AgentBridgeService
    from agent_bridge.automation.workflows.runner import FakeWorkflowRunner
    from agent_bridge.automation.workflows.scheduler import WorkflowScheduler

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
    from agent_bridge.app.service import AgentBridgeService
    from agent_bridge.automation.workflows.scheduler import WorkflowScheduler

    svc = AgentBridgeService.create(wm_paths, {"root"})
    scheduler = WorkflowScheduler(
        service=svc.workflows, store=svc.store, admins={"root"}, base_run_dir=tmp_path,
    )

    with pytest.raises(NotFound):
        scheduler.run_workflow_now("does-not-exist")


def test_run_workflow_now_bypasses_disabled_status(wm_paths, tmp_path):
    from agent_bridge.app.service import AgentBridgeService
    from agent_bridge.automation.workflows.runner import FakeWorkflowRunner
    from agent_bridge.automation.workflows.scheduler import WorkflowScheduler

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    svc.store.upsert_workflow_definition(
        workflow_key="A", name="A", description="", profile_key="report-plane",
        workflow_js="",
        status="disabled", created_by="root",
    )

    scheduler = WorkflowScheduler(
        service=svc.workflows, store=svc.store, admins={"root"},
        runner=FakeWorkflowRunner(status="no_executable_task"), base_run_dir=tmp_path,
    )

    result = scheduler.run_workflow_now("A")  # disabled, yet runnable for a test
    assert result["status"] == "started"


class _AlwaysFailingRunner:
    """Runner that always raises. The run row lands as 'failed' and the workflow
    never enters finished_today (only a no_task result does), so it can be
    re-scheduled every tick. Avoids the ingest/backend path entirely, keeping
    run-count tests focused on scheduling decisions."""

    def run(self, base_dir, spec):
        raise RuntimeError("boom")


def _bootstrap_svc_with_workflow(wm_paths, key="A"):
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    _create_workflow(svc.store, key)
    return svc


def _build_in_window_scheduler(svc, tmp_path, runner, max_runs=0):
    from agent_bridge.automation.workflows.scheduler import WorkflowScheduler

    now = datetime.now()
    scheduler = WorkflowScheduler(
        service=svc.workflows,
        store=svc.store,
        admins={"root"},
        runner=runner,
        base_run_dir=tmp_path,
        max_concurrent_workflows=2,
    )
    scheduler._start_time = (now - timedelta(minutes=1)).time().replace(second=0, microsecond=0)
    scheduler._stop_time = (now + timedelta(minutes=1)).time().replace(second=0, microsecond=0)
    scheduler._max_runs = max_runs
    return scheduler


def test_max_runs_zero_does_not_limit_scheduling(wm_paths, tmp_path):
    svc = _bootstrap_svc_with_workflow(wm_paths)
    scheduler = _build_in_window_scheduler(svc, tmp_path, _AlwaysFailingRunner(), max_runs=0)

    for _ in range(3):
        scheduler.tick()
        _wait_runs_done(scheduler)

    assert len(svc.store.list_workflow_runs("A")) == 3
    assert scheduler.run_counts == {}


def test_max_runs_caps_scheduling_per_window(wm_paths, tmp_path):
    svc = _bootstrap_svc_with_workflow(wm_paths)
    scheduler = _build_in_window_scheduler(svc, tmp_path, _AlwaysFailingRunner(), max_runs=2)

    for _ in range(3):
        scheduler.tick()
        _wait_runs_done(scheduler)

    assert scheduler.run_counts == {"A": 2}
    assert len(svc.store.list_workflow_runs("A")) == 2


def test_manual_run_does_not_count_and_bypasses_limit(wm_paths, tmp_path):
    from agent_bridge.automation.workflows.runner import FakeWorkflowRunner

    svc = _bootstrap_svc_with_workflow(wm_paths)
    scheduler = _build_in_window_scheduler(
        svc, tmp_path, FakeWorkflowRunner(status="no_executable_task"), max_runs=1
    )

    scheduler.tick()
    _wait_runs_done(scheduler)
    assert scheduler.run_counts == {"A": 1}

    result = scheduler.run_workflow_now("A")
    assert result["status"] == "started"
    _wait_runs_done(scheduler)
    assert scheduler.run_counts == {"A": 1}  # manual run did not increment the cap


def test_window_reset_clears_run_counts(wm_paths, tmp_path):
    svc = _bootstrap_svc_with_workflow(wm_paths)
    scheduler = _build_in_window_scheduler(svc, tmp_path, _AlwaysFailingRunner(), max_runs=5)

    scheduler.tick()
    _wait_runs_done(scheduler)
    scheduler.tick()
    _wait_runs_done(scheduler)
    assert scheduler.run_counts == {"A": 2}

    scheduler._window_marker = None  # force the new-window reset branch on next tick
    scheduler.tick()
    _wait_runs_done(scheduler)
    assert scheduler.run_counts == {"A": 1}  # reset to 0, then one fresh scheduled run


def test_sync_config_round_trips_workflow_max_runs(wm_paths):
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.save_sync_config(
        code_sync_cron="0 * * * *",
        understand_cron="0 2 * * *",
        doc_sync_cron="*/30 * * * *",
        workflow_start_time="22:00",
        workflow_stop_time="07:00",
        workflow_max_runs=10,
    )
    config = svc.store.get_sync_config()
    assert config["workflow_max_runs"] == 10


def test_sync_config_round_trips_workflow_task_rerun_days(wm_paths):
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.save_sync_config(
        actor="root",
        code_sync_cron="0 * * * *",
        ua_git_url="",
        understand_cron="0 2 * * *",
        doc_sync_cron="*/30 * * * *",
        workflow_start_time="22:00",
        workflow_stop_time="07:00",
        workflow_max_runs=10,
        workflow_task_rerun_days=45,
    )

    config = svc.store.get_sync_config()
    assert config["workflow_task_rerun_days"] == 45


def test_scheduler_reads_workflow_max_runs_from_config(wm_paths):
    from agent_bridge.app.service import AgentBridgeService
    from agent_bridge.automation.workflows.runner import FakeWorkflowRunner
    from agent_bridge.automation.workflows.scheduler import WorkflowScheduler

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.save_sync_config(
        code_sync_cron="0 * * * *",
        understand_cron="0 2 * * *",
        doc_sync_cron="*/30 * * *",
        workflow_start_time="22:00",
        workflow_stop_time="07:00",
        workflow_max_runs=7,
    )
    scheduler = WorkflowScheduler(
        service=svc.workflows, store=svc.store, admins={"root"}, runner=FakeWorkflowRunner()
    )
    scheduler._load_window()
    assert scheduler._max_runs == 7


def test_get_status_exposes_max_runs_and_run_counts(wm_paths, tmp_path):
    svc = _bootstrap_svc_with_workflow(wm_paths)
    scheduler = _build_in_window_scheduler(svc, tmp_path, _AlwaysFailingRunner(), max_runs=3)
    scheduler.tick()
    _wait_runs_done(scheduler)

    status = scheduler.get_status()
    assert status["max_runs"] == 3
    assert status["run_counts"] == {"A": 1}


def test_sync_config_round_trips_runtime_and_understand_timeout(wm_paths):
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.save_sync_config(
        actor="root",
        code_sync_cron="0 * * * *",
        ua_git_url="",
        understand_cron="0 2 * * *",
        doc_sync_cron="*/30 * * * *",
        workflow_start_time="22:00",
        workflow_stop_time="07:00",
        workflow_max_runtime_minutes=45,
        mcp_timeout_seconds=150,
        understand_timeout_minutes=90,
    )

    config = svc.store.get_sync_config()
    assert config["workflow_max_runtime_minutes"] == 45
    assert config["mcp_timeout_seconds"] == 150
    assert config["understand_timeout_minutes"] == 90


def test_sync_config_defaults_mcp_timeout_seconds_to_150(wm_paths):
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()

    config = svc.store.get_sync_config()

    assert config["mcp_timeout_seconds"] == 150


def test_scheduler_reads_workflow_max_runtime_from_config(wm_paths):
    from agent_bridge.app.service import AgentBridgeService
    from agent_bridge.automation.workflows.runner import FakeWorkflowRunner
    from agent_bridge.automation.workflows.scheduler import WorkflowScheduler

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.save_sync_config(
        code_sync_cron="0 * * * *",
        understand_cron="0 2 * * *",
        doc_sync_cron="*/30 * * *",
        workflow_start_time="22:00",
        workflow_stop_time="07:00",
        workflow_max_runtime_minutes=30,
    )
    scheduler = WorkflowScheduler(
        service=svc.workflows, store=svc.store, admins={"root"}, runner=FakeWorkflowRunner()
    )
    scheduler._load_window()
    assert scheduler._max_runtime_minutes == 30
    assert scheduler.get_status()["max_runtime_minutes"] == 30
