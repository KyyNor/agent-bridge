from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator

import pytest


class _RecordingConnection:
    def __init__(self, conn: sqlite3.Connection, statements: list[str]) -> None:
        self._conn = conn
        self._statements = statements

    def execute(self, sql: str, parameters=()):
        self._statements.append(" ".join(sql.split()).upper())
        return self._conn.execute(sql, parameters)


def test_workflow_definition_requires_profile_reference(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()

    with pytest.raises(sqlite3.IntegrityError):
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
    assert first == {"created": 1, "updated": 0, "skipped_completed": 0, "skipped_running": 0}
    assert second == {"created": 0, "updated": 1, "skipped_completed": 0, "skipped_running": 0}

    task = store.lease_workflow_task("page-report", run_id="run_1", lease_seconds=7200)
    assert task is not None
    store.complete_workflow_task("page-report", "page:a", run_id="run_1")

    third = store.upsert_workflow_tasks(
        "page-report",
        [{"task_key": "page:a", "payload": {"page": "a3"}}],
    )
    assert third == {"created": 0, "updated": 0, "skipped_completed": 1, "skipped_running": 0}
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


def test_workflow_task_upsert_does_not_release_active_lease(wm_paths):
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
    store.upsert_workflow_tasks("page-report", [{"task_key": "page:a", "payload": {"page": "a"}}])

    leased = store.lease_workflow_task("page-report", run_id="run_1", lease_seconds=7200)
    result = store.upsert_workflow_tasks(
        "page-report",
        [{"task_key": "page:a", "payload": {"page": "a2"}}],
    )

    assert leased["lease_run_id"] == "run_1"
    assert result == {"created": 0, "updated": 0, "skipped_completed": 0, "skipped_running": 1}
    task = store.get_workflow_task("page-report", "page:a")
    assert task["status"] == "running"
    assert task["lease_run_id"] == "run_1"
    assert task["lease_expires_at"] == leased["lease_expires_at"]
    assert task["payload"]["page"] == "a"
    assert store.lease_workflow_task("page-report", run_id="run_2", lease_seconds=7200) is None


def test_workflow_task_upsert_reopens_expired_running_task(wm_paths):
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
    store.upsert_workflow_tasks("page-report", [{"task_key": "page:a", "payload": {"page": "a"}}])
    store.lease_workflow_task("page-report", run_id="run_1", lease_seconds=7200)

    expired = datetime.now(timezone.utc) - timedelta(minutes=1)
    store.force_workflow_task_lease_expiry("page-report", "page:a", expired.isoformat())
    result = store.upsert_workflow_tasks(
        "page-report",
        [{"task_key": "page:a", "payload": {"page": "a2"}}],
    )

    assert result == {"created": 0, "updated": 1, "skipped_completed": 0, "skipped_running": 0}
    task = store.get_workflow_task("page-report", "page:a")
    assert task["status"] == "pending"
    assert task["lease_run_id"] is None
    assert task["lease_expires_at"] is None
    assert task["payload"]["page"] == "a2"


def test_workflow_task_complete_requires_current_lease_owner(wm_paths):
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

    store.lease_workflow_task("page-report", run_id="run_1", lease_seconds=7200)
    expired = datetime.now(timezone.utc) - timedelta(minutes=1)
    store.force_workflow_task_lease_expiry("page-report", "page:a", expired.isoformat())
    store.lease_workflow_task("page-report", run_id="run_2", lease_seconds=7200)

    assert store.complete_workflow_task("page-report", "page:a", run_id="run_1") is False
    task = store.get_workflow_task("page-report", "page:a")
    assert task["status"] == "running"
    assert task["lease_run_id"] == "run_2"

    assert store.complete_workflow_task("page-report", "page:a", run_id="run_2") is True
    assert store.get_workflow_task("page-report", "page:a")["status"] == "completed"


def test_workflow_task_upsert_uses_immediate_transaction_before_read(wm_paths):
    from agent_bridge.storage.repositories.workflows import WorkflowsRepository
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
    store.upsert_workflow_tasks("page-report", [{"task_key": "page:a", "payload": {"page": "a"}}])

    statements: list[str] = []

    @contextmanager
    def recording_connect() -> Iterator[_RecordingConnection]:
        conn = sqlite3.connect(wm_paths.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield _RecordingConnection(conn, statements)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    repo = WorkflowsRepository(wm_paths.db_path, recording_connect)
    result = repo.upsert_workflow_tasks(
        "page-report",
        [{"task_key": "page:a", "payload": {"page": "a2"}}],
    )

    assert result == {"created": 0, "updated": 1, "skipped_completed": 0, "skipped_running": 0}
    assert statements[0] == "BEGIN IMMEDIATE"
    assert statements[1].startswith("SELECT STATUS, LEASE_EXPIRES_AT")
