"""Route-level coverage for execute / reset (features 3 & 4).

The reset endpoint has no agent side-effects and is safe to exercise end to
end. The execute endpoint starts a real workflow run, so here we only assert
its pre-flight validation (it short-circuits before touching the scheduler
when the task is not leasable / unknown), and that the priority flag is set on
the happy path via the service-layer suite.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi.testclient import TestClient


def _client(wm_paths):
    from agent_bridge.api.app import create_app
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
    return svc, TestClient(create_app(wm_paths, {"root"}))


H = {"X-Agent-Bridge-User": "root"}


def test_reset_endpoint_restores_abandoned_task(wm_paths):
    svc, client = _client(wm_paths)
    svc.store.upsert_workflow_tasks("w", [{"task_key": "page:a", "payload": {}}])
    svc.store.create_workflow_run(
        run_id="run_1",
        workflow_key="w",
        profile_key="report-plane",
        task_key=None,
        status="running",
        temp_dir="/tmp/run_1",
    )
    svc.store.lease_workflow_task("w", run_id="run_1", lease_seconds=7200)
    svc.store.release_or_abandon_tasks_for_run("w", "run_1", max_attempts=0, error_message="boom")
    assert svc.store.get_workflow_task("w", "page:a")["status"] == "abandoned"

    resp = client.post("/workflows/w/tasks/page:a/reset", headers=H)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    assert body["task_key"] == "page:a"
    assert svc.store.get_workflow_task("w", "page:a")["status"] == "pending"


def test_execute_endpoint_rejects_unknown_task(wm_paths):
    svc, client = _client(wm_paths)
    svc.store.upsert_workflow_tasks("w", [{"task_key": "page:a", "payload": {}}])
    resp = client.post("/workflows/w/tasks/nope/execute", headers=H)
    assert resp.status_code == 404, resp.text


def test_execute_endpoint_rejects_completed_task_without_reset(wm_paths):
    """A completed task is not executable; the endpoint must 400 (validation),
    and crucially must NOT reach the scheduler / start a run."""
    svc, client = _client(wm_paths)
    svc.store.upsert_workflow_tasks("w", [{"task_key": "page:a", "payload": {}}])
    svc.store.create_workflow_run(
        run_id="run_1",
        workflow_key="w",
        profile_key="report-plane",
        task_key=None,
        status="running",
        temp_dir="/tmp/run_1",
    )
    svc.store.lease_workflow_task("w", run_id="run_1", lease_seconds=7200)
    svc.store.complete_workflow_task("w", "page:a", run_id="run_1")

    resp = client.post("/workflows/w/tasks/page:a/execute", headers=H)
    assert resp.status_code == 400, resp.text
    # No new run created (execute short-circuited before the scheduler).
    assert len(svc.store.list_workflow_runs("w", limit=10)) == 1


def test_reset_endpoint_rejects_active_running_task(wm_paths):
    svc, client = _client(wm_paths)
    svc.store.upsert_workflow_tasks("w", [{"task_key": "page:a", "payload": {}}])
    svc.store.create_workflow_run(
        run_id="run_1",
        workflow_key="w",
        profile_key="report-plane",
        task_key=None,
        status="running",
        temp_dir="/tmp/run_1",
    )
    svc.store.lease_workflow_task("w", run_id="run_1", lease_seconds=7200)

    resp = client.post("/workflows/w/tasks/page:a/reset", headers=H)
    assert resp.status_code == 400, resp.text
    task = svc.store.get_workflow_task("w", "page:a")
    assert task["status"] == "running"
    assert task["lease_run_id"] == "run_1"


def test_reset_endpoint_supports_task_keys_with_slashes(wm_paths):
    svc, client = _client(wm_paths)
    task_key = "reports/page:a"
    svc.store.upsert_workflow_tasks("w", [{"task_key": task_key, "payload": {}}])
    svc.store.set_priority_for_task("w", task_key)

    resp = client.post(f"/workflows/w/tasks/{quote(task_key, safe='')}/reset", headers=H)
    assert resp.status_code == 200, resp.text
    assert resp.json()["task_key"] == task_key
    assert svc.store.get_workflow_task("w", task_key)["priority_flag"] is None


def test_reset_endpoint_requires_admin(wm_paths):
    svc, client = _client(wm_paths)
    svc.store.upsert_workflow_tasks("w", [{"task_key": "page:a", "payload": {}}])
    resp = client.post("/workflows/w/tasks/page:a/reset", headers={"X-Agent-Bridge-User": "intruder"})
    assert resp.status_code == 403, resp.text
