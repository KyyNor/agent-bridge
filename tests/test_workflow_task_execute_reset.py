"""execute_task / reset_task at the service layer (features 3 & 4)."""

from __future__ import annotations

import pytest

from agent_bridge.core.domain import NotFound, ValidationError


def _service(wm_paths):
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    svc.workflows.upsert_definition(
        actor="root",
        workflow_key="w",
        name="w",
        description="",
        profile_key="report-plane",
        status="active",
    )
    return svc


def _seed_run(store, run_id="run_1"):
    store.create_workflow_run(
        run_id=run_id,
        workflow_key="w",
        profile_key="report-plane",
        task_key=None,
        status="running",
        temp_dir=f"/tmp/{run_id}",
    )


# ---------------------------------------------------------------------------
# execute_task
# ---------------------------------------------------------------------------

def test_execute_task_sets_priority_flag_on_pending_task(wm_paths):
    svc = _service(wm_paths)
    svc.store.upsert_workflow_tasks(
        "w",
        [{"task_key": "page:a", "payload": {}}, {"task_key": "page:b", "payload": {}}],
    )

    result = svc.workflows.execute_task(actor="root", workflow_key="w", task_key="page:b")

    assert result["workflow_key"] == "w"
    assert result["task_key"] == "page:b"
    assert result["priority"] is True
    a = svc.store.get_workflow_task("w", "page:a")
    b = svc.store.get_workflow_task("w", "page:b")
    assert a["priority_flag"] is None
    assert b["priority_flag"] is not None


def test_execute_task_requires_admin(wm_paths):
    svc = _service(wm_paths)
    svc.store.upsert_workflow_tasks("w", [{"task_key": "page:a", "payload": {}}])
    with pytest.raises(Exception):
        svc.workflows.execute_task(actor="intruder", workflow_key="w", task_key="page:a")


def test_execute_task_unknown_workflow_raises_not_found(wm_paths):
    svc = _service(wm_paths)
    with pytest.raises(NotFound):
        svc.workflows.execute_task(actor="root", workflow_key="missing", task_key="page:a")


def test_execute_task_unknown_task_raises_not_found(wm_paths):
    svc = _service(wm_paths)
    svc.store.upsert_workflow_tasks("w", [{"task_key": "page:a", "payload": {}}])
    with pytest.raises(NotFound):
        svc.workflows.execute_task(actor="root", workflow_key="w", task_key="nope")


def test_execute_task_rejects_non_leasable_completed_task(wm_paths):
    """A completed task is not immediately executable; the user must reset first."""
    svc = _service(wm_paths)
    svc.store.upsert_workflow_tasks("w", [{"task_key": "page:a", "payload": {}}])
    _seed_run(svc.store)
    svc.store.lease_workflow_task("w", run_id="run_1", lease_seconds=7200)
    svc.store.complete_workflow_task("w", "page:a", run_id="run_1")

    with pytest.raises(ValidationError):
        svc.workflows.execute_task(actor="root", workflow_key="w", task_key="page:a")


def test_execute_task_rejects_actively_running_task(wm_paths):
    """A task mid-lease (running, not expired) cannot be priority-jumped without reset."""
    svc = _service(wm_paths)
    svc.store.upsert_workflow_tasks("w", [{"task_key": "page:a", "payload": {}}])
    _seed_run(svc.store)
    svc.store.lease_workflow_task("w", run_id="run_1", lease_seconds=7200)

    with pytest.raises(ValidationError):
        svc.workflows.execute_task(actor="root", workflow_key="w", task_key="page:a")


def test_execute_task_supports_task_version(wm_paths):
    svc = _service(wm_paths)
    svc.store.upsert_workflow_tasks(
        "w",
        [{"task_key": "page:a", "task_version": "v1", "payload": {}}, {"task_key": "page:a", "task_version": "v2", "payload": {}}],
    )
    svc.workflows.execute_task(actor="root", workflow_key="w", task_key="page:a", task_version="v2")
    assert svc.store.get_workflow_task("w", "page:a", task_version="v1")["priority_flag"] is None
    assert svc.store.get_workflow_task("w", "page:a", task_version="v2")["priority_flag"] is not None


def test_execute_task_without_version_prioritizes_resolved_latest_version(wm_paths):
    """When task_version is omitted, the API reports the latest row and must
    priority-stamp that same row only."""
    svc = _service(wm_paths)
    svc.store.upsert_workflow_tasks(
        "w",
        [{"task_key": "page:a", "task_version": "v1", "payload": {}}, {"task_key": "page:a", "task_version": "v2", "payload": {}}],
    )

    result = svc.workflows.execute_task(actor="root", workflow_key="w", task_key="page:a")

    assert result["task_version"] == "v2"
    assert svc.store.get_workflow_task("w", "page:a", task_version="v1")["priority_flag"] is None
    assert svc.store.get_workflow_task("w", "page:a", task_version="v2")["priority_flag"] is not None
    leased = svc.store.lease_workflow_task("w", run_id="run_1", lease_seconds=7200)
    assert leased["task_version"] == "v2"


# ---------------------------------------------------------------------------
# reset_task
# ---------------------------------------------------------------------------

def test_reset_task_restores_abandoned_to_pending(wm_paths):
    svc = _service(wm_paths)
    svc.store.upsert_workflow_tasks("w", [{"task_key": "page:a", "payload": {}}])
    _seed_run(svc.store)
    svc.store.lease_workflow_task("w", run_id="run_1", lease_seconds=7200)
    svc.store.release_or_abandon_tasks_for_run("w", "run_1", max_attempts=0, error_message="boom")
    assert svc.store.get_workflow_task("w", "page:a")["status"] == "abandoned"

    result = svc.workflows.reset_task(actor="root", workflow_key="w", task_key="page:a")

    assert result["status"] == "pending"
    task = svc.store.get_workflow_task("w", "page:a")
    assert task["status"] == "pending"
    assert task["lease_run_id"] is None
    assert task["completed_at"] is None


def test_reset_task_clears_priority_flag(wm_paths):
    svc = _service(wm_paths)
    svc.store.upsert_workflow_tasks("w", [{"task_key": "page:a", "payload": {}}])
    svc.store.set_priority_for_task("w", "page:a")
    assert svc.store.get_workflow_task("w", "page:a")["priority_flag"] is not None

    svc.workflows.reset_task(actor="root", workflow_key="w", task_key="page:a")
    assert svc.store.get_workflow_task("w", "page:a")["priority_flag"] is None


def test_reset_task_preserves_attempt_count(wm_paths):
    svc = _service(wm_paths)
    svc.store.upsert_workflow_tasks("w", [{"task_key": "page:a", "payload": {}}])
    _seed_run(svc.store)
    svc.store.lease_workflow_task("w", run_id="run_1", lease_seconds=7200)  # attempt_count -> 1
    svc.store.release_or_abandon_tasks_for_run("w", "run_1", max_attempts=0, error_message="x")
    before = svc.store.get_workflow_task("w", "page:a")["attempt_count"]
    assert before == 1

    svc.workflows.reset_task(actor="root", workflow_key="w", task_key="page:a")
    assert svc.store.get_workflow_task("w", "page:a")["attempt_count"] == 1


def test_reset_task_rejects_active_running_task(wm_paths):
    svc = _service(wm_paths)
    svc.store.upsert_workflow_tasks("w", [{"task_key": "page:a", "payload": {}}])
    _seed_run(svc.store)
    svc.store.lease_workflow_task("w", run_id="run_1", lease_seconds=7200)

    with pytest.raises(ValidationError):
        svc.workflows.reset_task(actor="root", workflow_key="w", task_key="page:a")


def test_reset_task_without_version_resets_resolved_latest_version_only(wm_paths):
    svc = _service(wm_paths)
    svc.store.upsert_workflow_tasks(
        "w",
        [{"task_key": "page:a", "task_version": "v1", "payload": {}}, {"task_key": "page:a", "task_version": "v2", "payload": {}}],
    )
    svc.store.set_priority_for_task("w", "page:a", task_version="v1")
    svc.store.set_priority_for_task("w", "page:a", task_version="v2")

    result = svc.workflows.reset_task(actor="root", workflow_key="w", task_key="page:a")

    assert result["task_version"] == "v2"
    assert svc.store.get_workflow_task("w", "page:a", task_version="v1")["priority_flag"] is not None
    assert svc.store.get_workflow_task("w", "page:a", task_version="v2")["priority_flag"] is None


def test_reset_task_unknown_task_raises_not_found(wm_paths):
    svc = _service(wm_paths)
    with pytest.raises(NotFound):
        svc.workflows.reset_task(actor="root", workflow_key="w", task_key="nope")


def test_reset_task_requires_admin(wm_paths):
    svc = _service(wm_paths)
    svc.store.upsert_workflow_tasks("w", [{"task_key": "page:a", "payload": {}}])
    with pytest.raises(Exception):
        svc.workflows.reset_task(actor="intruder", workflow_key="w", task_key="page:a")
