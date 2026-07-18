from __future__ import annotations


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
