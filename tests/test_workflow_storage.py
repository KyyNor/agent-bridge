from __future__ import annotations

from datetime import datetime, timedelta, timezone


def test_workflow_definition_requires_profile_reference(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()

    try:
        store.upsert_workflow_definition(
            workflow_key="page-report",
            name="Page Report",
            description="",
            profile_key="missing-profile",
            workflow_js="export const manifest = {};",
            manifest={"name": "Page Report", "nodes": [], "edges": [], "schemas": {}},
            schedule={"enabled": True, "start_time": "22:00", "stop_time": "07:00"},
            status="active",
            created_by="root",
        )
    except Exception as exc:
        assert "FOREIGN KEY" in str(exc) or "foreign key" in str(exc).lower()
    else:
        raise AssertionError("workflow definition without profile should fail")


def test_workflow_definition_round_trips_with_manifest_and_schedule(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.upsert_project_profile(
        profile_key="report-plane",
        name="Report Plane",
        description="",
        status="active",
        created_by="root",
    )

    created = store.upsert_workflow_definition(
        workflow_key="page-report",
        name="Page Report",
        description="Nightly page report",
        profile_key="report-plane",
        workflow_js="export const manifest = { name: 'Page Report' };",
        manifest={"name": "Page Report", "nodes": [{"id": "get_task"}], "edges": [], "schemas": {}},
        schedule={"enabled": True, "start_time": "22:00", "stop_time": "07:00"},
        status="active",
        created_by="root",
    )

    assert created["workflow_key"] == "page-report"
    assert created["profile_key"] == "report-plane"
    assert created["manifest"]["nodes"] == [{"id": "get_task"}]
    assert created["schedule"]["stop_time"] == "07:00"

    listed = store.list_workflow_definitions()
    assert [item["workflow_key"] for item in listed] == ["page-report"]


def test_workflow_task_upsert_is_idempotent_and_does_not_replace_completed(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    store.upsert_workflow_definition(
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        workflow_js="",
        manifest={"name": "Page Report", "nodes": [], "edges": [], "schemas": {}},
        schedule={"enabled": True, "start_time": "22:00", "stop_time": "07:00"},
        status="active",
        created_by="root",
    )

    first = store.upsert_workflow_tasks(
        "page-report",
        [{"task_key": "page:a", "payload": {"page": "a"}}],
    )
    second = store.upsert_workflow_tasks(
        "page-report",
        [{"task_key": "page:a", "payload": {"page": "a2"}}],
    )
    assert first == {"created": 1, "updated": 0, "skipped_completed": 0}
    assert second == {"created": 0, "updated": 1, "skipped_completed": 0}

    task = store.lease_workflow_task("page-report", run_id="run_1", lease_seconds=7200)
    assert task is not None
    store.complete_workflow_task("page-report", "page:a", run_id="run_1")

    third = store.upsert_workflow_tasks(
        "page-report",
        [{"task_key": "page:a", "payload": {"page": "a3"}}],
    )
    assert third == {"created": 0, "updated": 0, "skipped_completed": 1}
    assert store.get_workflow_task("page-report", "page:a")["payload"]["page"] == "a2"


def test_workflow_task_lease_is_exclusive_and_expired_leases_are_reclaimed(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    store.upsert_workflow_definition(
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        workflow_js="",
        manifest={"name": "Page Report", "nodes": [], "edges": [], "schemas": {}},
        schedule={"enabled": True, "start_time": "22:00", "stop_time": "07:00"},
        status="active",
        created_by="root",
    )
    store.upsert_workflow_tasks("page-report", [{"task_key": "page:a", "payload": {}}])

    task = store.lease_workflow_task("page-report", run_id="run_1", lease_seconds=7200)
    assert task["task_key"] == "page:a"
    assert store.lease_workflow_task("page-report", run_id="run_2", lease_seconds=7200) is None

    expired = datetime.now(timezone.utc) - timedelta(minutes=1)
    store.force_workflow_task_lease_expiry("page-report", "page:a", expired.isoformat())
    reclaimed = store.lease_workflow_task("page-report", run_id="run_2", lease_seconds=7200)
    assert reclaimed["task_key"] == "page:a"
    assert reclaimed["lease_run_id"] == "run_2"
