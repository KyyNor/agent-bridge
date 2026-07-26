from __future__ import annotations


def test_delete_workflow_definition_removes_run_logs_without_foreign_key(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    store.upsert_workflow_definition(
        workflow_key="delete-me",
        name="Delete Me",
        description="",
        profile_key="report-plane",
        definition={"nodes": [], "edges": []},
        status="active",
        created_by="root",
    )
    store.create_workflow_run(
        run_id="run-delete-me",
        workflow_key="delete-me",
        profile_key="report-plane",
        task_key=None,
        status="completed",
        temp_dir="",
    )
    store.append_workflow_run_log(
        run_id="run-delete-me",
        workflow_key="delete-me",
        task_key=None,
        level="info",
        stage="test",
        message="delete this log",
        payload={},
    )

    assert store.delete_workflow_definition("delete-me") is True

    with store.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM workflow_run_logs WHERE workflow_key = ?",
            ("delete-me",),
        ).fetchone()[0] == 0


def test_workflow_definition_snapshot_and_node_runs_round_trip(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    definition = {"nodes": [], "edges": []}
    store.upsert_workflow_definition(workflow_key="report", name="Report", description="", profile_key="report-plane", definition=definition, status="active", created_by="root")
    run = store.create_workflow_run(run_id="run_1", workflow_key="report", profile_key="report-plane", task_key=None, status="running", temp_dir="", definition_snapshot=definition, input_data={"topic": "x"})
    store.create_workflow_node_runs("run_1", [{"node_id": "a", "node_type": "agent"}])
    store.start_workflow_node_run("run_1", "a")
    store.finish_workflow_node_run("run_1", "a", status="completed", output={"text": "ok"})
    assert run["definition_snapshot"] == definition
    assert store.list_workflow_node_runs("run_1")[0]["output"] == {"text": "ok"}


def test_workflow_incremental_storage_migrates_legacy_tables_idempotently(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    wm_paths.db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(wm_paths.db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE workflow_tasks (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              workflow_key TEXT NOT NULL,
              task_key TEXT NOT NULL,
              task_version TEXT NOT NULL DEFAULT '',
              type TEXT NOT NULL DEFAULT '',
              payload_json TEXT NOT NULL DEFAULT '{}',
              status TEXT NOT NULL DEFAULT 'pending',
              lease_run_id TEXT,
              lease_expires_at TEXT,
              attempt_count INTEGER NOT NULL DEFAULT 0,
              last_error TEXT,
              set_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              completed_at TEXT,
              priority_flag TEXT,
              UNIQUE (workflow_key, task_key, task_version)
            );
            CREATE TABLE workflow_runs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              run_id TEXT NOT NULL UNIQUE,
              workflow_key TEXT NOT NULL,
              profile_key TEXT NOT NULL,
              task_key TEXT,
              status TEXT NOT NULL,
              temp_dir TEXT NOT NULL DEFAULT '',
              definition_snapshot_json TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
              input_json TEXT NOT NULL DEFAULT '{}',
              output_json TEXT NOT NULL DEFAULT '{}',
              exit_code INTEGER,
              stdout_path TEXT,
              stderr_path TEXT,
              error TEXT,
              started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              finished_at TEXT,
              duration_ms INTEGER
            );
            CREATE TABLE workflow_node_runs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              run_id TEXT NOT NULL,
              node_id TEXT NOT NULL,
              node_type TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending',
              condition_results_json TEXT NOT NULL DEFAULT '[]',
              output_json TEXT NOT NULL DEFAULT '{}',
              error TEXT,
              agent_run_key TEXT,
              script_run_id TEXT,
              started_at TEXT,
              finished_at TEXT,
              UNIQUE (run_id, node_id)
            );
            """
        )

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.init_schema()

    with store.connect() as conn:
        columns = {
            table: {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            for table in ("workflow_tasks", "workflow_runs", "workflow_node_runs")
        }
        association_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(workflow_run_artifacts)")
        }
    assert "lease_origin_status" in columns["workflow_tasks"]
    assert {
        "workflow_revision_no",
        "workflow_content_hash",
        "task_version",
        "execution_mode",
        "execution_plan_json",
        "source_run_id",
    } <= columns["workflow_runs"]
    assert {
        "node_fingerprint",
        "action",
        "reuse_reason",
        "source_run_id",
        "source_node_id",
        "source_node_fingerprint",
        "artifact_ids_json",
    } <= columns["workflow_node_runs"]
    assert {
        "run_id",
        "node_id",
        "artifact_id",
        "source_run_id",
        "source_node_id",
        "created_at",
    } <= association_columns


def test_workflow_task_status_supports_stale_serialization():
    from agent_bridge.automation.workflows.models import WorkflowTaskStatus

    assert WorkflowTaskStatus.stale.value == "stale"
    assert str(WorkflowTaskStatus.stale.value) == "stale"


def test_workflow_task_lease_and_priority_only_consider_latest_set_at_version(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    _seed_workflow_with_task(store)
    store.upsert_workflow_tasks(
        "w",
        [
            {"task_key": "page:a", "task_version": "v1", "payload": {}},
            {"task_key": "page:a", "task_version": "v2", "payload": {}},
        ],
    )

    store.set_priority_for_task("w", "page:a")
    assert store.get_workflow_task("w", "page:a", task_version="v1")["priority_flag"] is None
    assert store.get_workflow_task("w", "page:a", task_version="v2")["priority_flag"] is not None

    leased = store.lease_workflow_task("w", run_id="run_1", lease_seconds=7200)
    assert leased["task_version"] == "v2"
    assert store.get_workflow_task("w", "page:a", task_version="v1")["status"] == "pending"


def test_exact_task_lease_does_not_fall_through_to_another_queue_item(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    _seed_workflow_with_task(store)
    store.upsert_workflow_tasks(
        "w",
        [{"task_key": "page:b", "task_version": "v1", "payload": {}}],
    )

    leased = store.lease_workflow_task_by_key(
        "w", "page:b", task_version="v1", run_id="run_exact", lease_seconds=7200
    )

    assert leased is not None
    assert leased["task_key"] == "page:b"
    assert leased["lease_run_id"] == "run_exact"
    assert store.get_workflow_task("w", "page:a", task_version="")["status"] == "pending"


def test_stale_lease_restores_origin_status_after_failure_and_clears_it_on_complete(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    _seed_workflow_with_task(store)
    with store.connect() as conn:
        conn.execute(
            "UPDATE workflow_tasks SET status = 'stale' WHERE workflow_key = ? AND task_key = ?",
            ("w", "page:a"),
        )

    leased = store.lease_workflow_task("w", run_id="run_1", lease_seconds=7200)
    assert leased["status"] == "running"
    assert leased["lease_origin_status"] == "stale"
    assert store.release_or_abandon_tasks_for_run(
        "w", "run_1", max_attempts=3, error_message="boom"
    ) == {"released": 1, "abandoned": 0}
    assert store.get_workflow_task("w", "page:a")["status"] == "stale"

    store.lease_workflow_task("w", run_id="run_2", lease_seconds=7200)
    assert store.complete_workflow_task("w", "page:a", run_id="run_2") is True
    assert store.get_workflow_task("w", "page:a")["lease_origin_status"] is None


def test_workflow_incremental_run_node_and_artifact_metadata_round_trip(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    _seed_workflow_with_task(store)
    run = store.create_workflow_run(
        run_id="run_2",
        workflow_key="w",
        profile_key="report-plane",
        task_key="page:a",
        status="running",
        temp_dir="",
        workflow_revision_no=4,
        workflow_content_hash="workflow-hash",
        task_version="v2",
        execution_mode="incremental",
        execution_plan={"nodes": [{"node_id": "a", "action": "reuse"}]},
        source_run_id="run_1",
    )
    store.create_workflow_node_runs(
        "run_2",
        [
            {
                "node_id": "a",
                "node_type": "agent",
                "node_fingerprint": "node-hash",
                "action": "reuse",
                "reuse_reason": "fingerprint_match",
                "source_run_id": "run_1",
                "source_node_id": "a",
                "source_node_fingerprint": "source-node-hash",
            }
        ],
    )
    node = store.finish_workflow_node_run(
        "run_2",
        "a",
        status="completed",
        output={"text": "reused"},
        artifact_ids=["artifact_1"],
    )
    store.associate_workflow_run_artifacts(
        "run_2",
        "a",
        ["artifact_1"],
        source_run_id="run_1",
        source_node_id="a",
    )
    store.associate_workflow_run_artifacts(
        "run_2",
        "a",
        ["artifact_1"],
        source_run_id="run_1",
        source_node_id="a",
    )

    assert run["execution_plan"] == {"nodes": [{"node_id": "a", "action": "reuse"}]}
    assert run["execution_mode"] == "incremental"
    assert node["artifact_ids"] == ["artifact_1"]
    assert node["action"] == "reuse"
    assert node["source_node_fingerprint"] == "source-node-hash"
    associations = store.list_workflow_run_artifacts("run_2", node_id="a")
    assert len(associations) == 1
    assert associations[0].items() >= {
        "run_id": "run_2",
        "node_id": "a",
        "artifact_id": "artifact_1",
        "source_run_id": "run_1",
        "source_node_id": "a",
    }.items()
    assert associations[0]["created_at"]

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator

import pytest


TASK_COUNTS_EMPTY = {
    "created": 0,
    "updated": 0,
    "skipped_completed": 0,
    "skipped_running": 0,
    "skipped_historical": 0,
    "reopened_expired": 0,
}


def task_counts(**overrides):
    result = dict(TASK_COUNTS_EMPTY)
    result.update(overrides)
    return result


def test_finish_workflow_run_uses_running_compare_and_set(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.upsert_project_profile(profile_key="p", name="P", created_by="root")
    store.upsert_workflow_definition(
        workflow_key="w",
        name="W",
        description="",
        profile_key="p",
        status="active",
        created_by="root",
    )
    store.create_workflow_run(
        run_id="run_cas",
        workflow_key="w",
        profile_key="p",
        task_key=None,
        status="completed",
        temp_dir="",
    )

    actual = store.finish_workflow_run(
        "run_cas",
        expected_status="running",
        status="stopped",
        exit_code=1,
        error="stop",
        duration_ms=2,
    )

    assert actual["status"] == "completed"


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
            status="active",
            created_by="root",
        )


def test_workflow_definition_round_trips_without_manifest(wm_paths):
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
        status="active",
        created_by="root",
    )

    assert created["workflow_key"] == "page-report"
    assert created["profile_key"] == "report-plane"
    assert "manifest" not in created
    assert "schedule" not in created

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
    assert first == task_counts(created=1)
    assert second == task_counts(updated=1)

    task = store.lease_workflow_task("page-report", run_id="run_1", lease_seconds=7200)
    assert task is not None
    store.complete_workflow_task("page-report", "page:a", run_id="run_1")

    third = store.upsert_workflow_tasks(
        "page-report",
        [{"task_key": "page:a", "payload": {"page": "a3"}}],
    )
    assert third == task_counts(skipped_completed=1)
    assert store.get_workflow_task("page-report", "page:a")["payload"]["page"] == "a2"


def test_workflow_task_version_allows_same_key_to_run_again(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    store.upsert_workflow_definition(
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        status="active",
        created_by="root",
    )

    first = store.upsert_workflow_tasks(
        "page-report",
        [{"task_key": "page:a", "task_version": "v1", "payload": {"page": "a"}}],
    )
    assert first == task_counts(created=1)

    leased = store.lease_workflow_task("page-report", run_id="run_1", lease_seconds=7200)
    assert leased["task_key"] == "page:a"
    assert leased["task_version"] == "v1"
    assert store.complete_workflow_task("page-report", "page:a", task_version="v1", run_id="run_1") is True

    same_version = store.upsert_workflow_tasks(
        "page-report",
        [{"task_key": "page:a", "task_version": "v1", "payload": {"page": "a-again"}}],
    )
    assert same_version == task_counts(skipped_completed=1)
    completed_preview = store.preview_workflow_task_actions(
        "page-report",
        [{"task_key": "page:a", "task_version": "v1", "payload": {"page": "a-again"}}],
    )
    assert completed_preview["rows"][0]["action"] == "skipped_completed"
    assert completed_preview["summary"] == task_counts(skipped_completed=1)

    next_version = store.upsert_workflow_tasks(
        "page-report",
        [{"task_key": "page:a", "task_version": "v2", "payload": {"page": "a-v2"}}],
    )
    assert next_version == task_counts(created=1)

    leased_again = store.lease_workflow_task("page-report", run_id="run_2", lease_seconds=7200)
    assert leased_again["task_key"] == "page:a"
    assert leased_again["task_version"] == "v2"
    assert leased_again["payload"]["page"] == "a-v2"


def test_workflow_task_completed_same_version_reopens_after_configured_rerun_window(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    store.save_sync_config(code_sync_cron="0 * * * *", workflow_task_rerun_days=30)
    store.upsert_workflow_definition(
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        status="active",
        created_by="root",
    )

    assert store.upsert_workflow_tasks(
        "page-report",
        [{"task_key": "page:a", "task_version": "v1", "payload": {"page": "old"}}],
    ) == task_counts(created=1)
    store.create_workflow_run(
        run_id="run_1",
        workflow_key="page-report",
        profile_key="report-plane",
        task_key=None,
        status="running",
        temp_dir="/tmp/run_1",
    )
    leased = store.lease_workflow_task("page-report", run_id="run_1", lease_seconds=7200)
    assert leased["task_key"] == "page:a"
    assert store.complete_workflow_task("page-report", "page:a", task_version="v1", run_id="run_1") is True

    old_set_at = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    with store.connect() as conn:
        conn.execute(
            """
            UPDATE workflow_tasks
            SET set_at = ?
            WHERE workflow_key = ? AND task_key = ? AND task_version = ?
            """,
            (old_set_at, "page-report", "page:a", "v1"),
        )

    rerun_preview = store.preview_workflow_task_actions(
        "page-report",
        [{"task_key": "page:a", "task_version": "v1", "payload": {"page": "new"}}],
    )
    assert rerun_preview["rows"][0]["action"] == "reopened_expired"
    assert rerun_preview["summary"] == task_counts(reopened_expired=1)

    reopened = store.upsert_workflow_tasks(
        "page-report",
        [{"task_key": "page:a", "task_version": "v1", "payload": {"page": "new"}}],
    )
    task = store.get_workflow_task("page-report", "page:a", task_version="v1")

    assert reopened == task_counts(reopened_expired=1)
    assert task["status"] == "pending"
    assert task["payload"] == {"page": "new"}
    assert task["lease_run_id"] is None
    assert task["lease_expires_at"] is None
    assert task["completed_at"] is None
    assert task["set_at"] > old_set_at


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


def test_release_tasks_for_stopped_run_is_exact_and_preserves_attempt_count(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    for workflow_key in ("workflow-a", "workflow-b"):
        store.upsert_workflow_definition(
            workflow_key=workflow_key,
            name=workflow_key,
            description="",
            profile_key="report-plane",
            status="active",
            created_by="root",
        )
    store.upsert_workflow_tasks(
        "workflow-a",
        [{"task_key": "stopped", "payload": {}}, {"task_key": "other-run", "payload": {}}],
    )
    store.upsert_workflow_tasks("workflow-b", [{"task_key": "same-run-id", "payload": {}}])

    stopped = store.lease_workflow_task("workflow-a", run_id="run_stopped", lease_seconds=7200)
    other = store.lease_workflow_task("workflow-a", run_id="run_other", lease_seconds=7200)
    cross_workflow = store.lease_workflow_task("workflow-b", run_id="run_stopped", lease_seconds=7200)
    assert stopped["attempt_count"] == 1
    assert other["lease_run_id"] == "run_other"
    assert cross_workflow["lease_run_id"] == "run_stopped"

    store.workflows.release_tasks_for_stopped_run(
        "workflow-a", "run_stopped", "运行已由用户停止"
    )

    released = store.get_workflow_task("workflow-a", "stopped")
    assert released["status"] == "pending"
    assert released["lease_run_id"] is None
    assert released["lease_expires_at"] is None
    assert released["last_error"] == "运行已由用户停止"
    assert released["attempt_count"] == 1
    assert store.get_workflow_task("workflow-a", "other-run")["status"] == "running"
    assert store.get_workflow_task("workflow-a", "other-run")["lease_run_id"] == "run_other"
    assert store.get_workflow_task("workflow-b", "same-run-id")["status"] == "running"


def test_workflow_task_lease_backfills_run_task_key(wm_paths):
    """Leasing a task stamps task_key onto the run's workflow_runs row.

    A run is created with task_key=None (scheduler / on-demand path); the
    run->task link must become queryable the moment a task is leased, not only
    the reverse task->run link that lease_run_id already provides.
    """
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    store.upsert_workflow_definition(
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        status="active",
        created_by="root",
    )
    store.upsert_workflow_tasks("page-report", [{"task_key": "page:a", "payload": {}}])
    store.create_workflow_run(
        run_id="run_1",
        workflow_key="page-report",
        profile_key="report-plane",
        task_key=None,
        status="running",
        temp_dir="",
    )
    assert store.get_workflow_run("run_1")["task_key"] is None

    leased = store.lease_workflow_task("page-report", run_id="run_1", lease_seconds=7200)
    assert leased["task_key"] == "page:a"

    assert store.get_workflow_run("run_1")["task_key"] == "page:a"


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
    assert result == task_counts(skipped_running=1)
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

    assert result == task_counts(updated=1)
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

    assert result == task_counts(updated=1)
    assert statements[0] == "BEGIN IMMEDIATE"
    # 重跑窗口走统一的 knowledge_sync_config 读取入口（全字段 SELECT）。
    assert "FROM KNOWLEDGE_SYNC_CONFIG" in statements[1]
    assert "WORKFLOW_TASK_RERUN_DAYS" in statements[1]
    assert statements[2].startswith("SELECT ID, STATUS, LEASE_EXPIRES_AT, SET_AT")


def _seed_workflow_with_task(store, workflow_key: str = "w", task_key: str = "page:a") -> None:
    store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    store.upsert_workflow_definition(
        workflow_key=workflow_key,
        name=workflow_key,
        description="",
        profile_key="report-plane",
        status="active",
        created_by="root",
    )
    store.upsert_workflow_tasks(workflow_key, [{"task_key": task_key, "payload": {}}])


def test_release_or_abandon_releases_running_task_below_threshold(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    _seed_workflow_with_task(store)
    store.lease_workflow_task("w", run_id="run_1", lease_seconds=7200)  # attempt_count -> 1

    result = store.release_or_abandon_tasks_for_run(
        "w", "run_1", max_attempts=3, error_message="boom"
    )

    assert result == {"released": 1, "abandoned": 0}
    task = store.get_workflow_task("w", "page:a")
    assert task["status"] == "pending"
    assert task["lease_run_id"] is None
    assert task["lease_expires_at"] is None
    assert task["last_error"] == "boom"
    assert task["attempt_count"] == 1


def test_release_or_abandon_abandons_task_above_threshold(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    _seed_workflow_with_task(store)
    # Bump attempt_count to 4 via repeated lease/release cycles.
    for _ in range(4):
        store.lease_workflow_task("w", run_id="run_1", lease_seconds=7200)
        store.release_or_abandon_tasks_for_run("w", "run_1", max_attempts=99, error_message="retry")

    task = store.get_workflow_task("w", "page:a")
    assert task["attempt_count"] == 4
    assert task["status"] == "pending"

    # One more lease (attempt_count -> 5), then abandon at threshold 3.
    store.lease_workflow_task("w", run_id="run_1", lease_seconds=7200)
    result = store.release_or_abandon_tasks_for_run(
        "w", "run_1", max_attempts=3, error_message="final"
    )

    assert result == {"released": 0, "abandoned": 1}
    task = store.get_workflow_task("w", "page:a")
    assert task["status"] == "abandoned"
    assert task["last_error"] == "final"


def test_workflow_artifacts_keep_history_and_mark_only_latest_version_current(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    store.upsert_workflow_definition(
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        status="active",
        created_by="root",
    )

    first = store.upsert_workflow_artifact(
        workflow_key="page-report",
        profile_key="report-plane",
        run_id="run_1",
        task_key="page:a",
        task_version="v1",
        title="Page A v1",
        path="pages/page-a.md",
        tags=["finance"],
        format="markdown",
        summary="v1 summary",
        content="# v1",
        metadata={},
    )
    second = store.upsert_workflow_artifact(
        workflow_key="page-report",
        profile_key="report-plane",
        run_id="run_2",
        task_key="page:a",
        task_version="v2",
        title="Page A v2",
        path="pages/page-a.md",
        tags=["finance"],
        format="markdown",
        summary="v2 summary",
        content="# v2",
        metadata={},
    )

    assert first["artifact_id"] != second["artifact_id"]
    assert store.get_workflow_artifact(first["artifact_id"])["is_current"] is False
    assert store.get_workflow_artifact(second["artifact_id"])["is_current"] is True

    current = store.search_workflow_artifacts(
        profile_key="report-plane",
        query=None,
        tags=[],
        path="pages/page-a.md",
        workflow_key="page-report",
        task_key="page:a",
        include_history=False,
        limit=10,
    )
    assert [item["task_version"] for item in current] == ["v2"]
    assert current[0]["content"] == "# v2"

    history = store.search_workflow_artifacts(
        profile_key="report-plane",
        query=None,
        tags=[],
        path="pages/page-a.md",
        workflow_key="page-report",
        task_key="page:a",
        include_history=True,
        limit=10,
    )
    assert [item["task_version"] for item in history] == ["v2", "v1"]


def test_workflow_artifact_jieba_fts5_search_and_index_sync(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    store.upsert_workflow_definition(
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        status="active",
        created_by="root",
    )

    saved = store.upsert_workflow_artifact(
        workflow_key="page-report",
        profile_key="report-plane",
        run_id="run_1",
        task_key="page:a",
        title="DietrichGebert/ponytail — 让 AI 编程助手化身「最懒资深工程师」的技能 / 插件",
        path="repos/AlexsJones/llmfit.md",
        tags=["finance"],
        format="markdown",
        summary="财务订单分析，最懒资深工程师",
        content="Uses finance_orders and monthly totals.",
        metadata={},
    )

    with store.connect() as conn:
        tokenizer = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'workflow_artifacts_fts'"
        ).fetchone()[0]
        assert "unicode61" in tokenizer
        assert "workflow_artifacts_search_content" in tokenizer

    english = store.search_workflow_artifacts(
        profile_key="report-plane",
        query="finance_orders",
        tags=[],
        path=None,
        workflow_key=None,
        include_history=False,
        limit=10,
    )
    assert [item["artifact_id"] for item in english] == [saved["artifact_id"]]

    prefix_path = store.search_workflow_artifacts(
        profile_key="report-plane",
        query="repo",
        tags=[],
        path=None,
        workflow_key=None,
        include_history=False,
        limit=10,
    )
    assert [item["artifact_id"] for item in prefix_path] == [saved["artifact_id"]]

    prefix_identifier = store.search_workflow_artifacts(
        profile_key="report-plane",
        query="llm",
        tags=[],
        path=None,
        workflow_key=None,
        include_history=False,
        limit=10,
    )
    assert [item["artifact_id"] for item in prefix_identifier] == [saved["artifact_id"]]

    prefix_identifier_with_underscore = store.search_workflow_artifacts(
        profile_key="report-plane",
        query="finance_order",
        tags=[],
        path=None,
        workflow_key=None,
        include_history=False,
        limit=10,
    )
    assert [item["artifact_id"] for item in prefix_identifier_with_underscore] == [saved["artifact_id"]]

    short_token = store.search_workflow_artifacts(
        profile_key="report-plane",
        query="re",
        tags=[],
        path=None,
        workflow_key=None,
        include_history=False,
        limit=10,
    )
    assert short_token == []

    infix_token = store.search_workflow_artifacts(
        profile_key="report-plane",
        query="fit",
        tags=[],
        path=None,
        workflow_key=None,
        include_history=False,
        limit=10,
    )
    assert infix_token == []

    path_match = store.search_workflow_artifacts(
        profile_key="report-plane",
        query="repos/AlexsJones/llmfit.md",
        tags=[],
        path=None,
        workflow_key=None,
        include_history=False,
        limit=10,
    )
    assert [item["artifact_id"] for item in path_match] == [saved["artifact_id"]]

    chinese = store.search_workflow_artifacts(
        profile_key="report-plane",
        query="财务",
        tags=[],
        path=None,
        workflow_key=None,
        include_history=False,
        limit=10,
    )
    assert [item["artifact_id"] for item in chinese] == [saved["artifact_id"]]

    compositional_chinese = store.search_workflow_artifacts(
        profile_key="report-plane",
        query="最懒工程师",
        tags=[],
        path=None,
        workflow_key=None,
        include_history=False,
        limit=10,
    )
    assert [item["artifact_id"] for item in compositional_chinese] == [saved["artifact_id"]]

    store.upsert_workflow_artifact(
        workflow_key="page-report",
        profile_key="report-plane",
        run_id="run_1",
        task_key="page:a",
        title="DietrichGebert/ponytail — 让 AI 编程助手化身「最懒资深工程师」的技能 / 插件",
        path="repos/AlexsJones/llmfit.md",
        tags=["finance"],
        format="markdown",
        summary="updated，最懒资深工程师",
        content="Uses revised_invoice_totals.",
        metadata={},
    )
    assert store.search_workflow_artifacts(
        profile_key="report-plane",
        query="finance_orders",
        tags=[],
        path=None,
        workflow_key=None,
        include_history=False,
        limit=10,
    ) == []
    assert len(
        store.search_workflow_artifacts(
            profile_key="report-plane",
            query="revised_invoice_totals",
            tags=[],
            path=None,
            workflow_key=None,
            include_history=False,
            limit=10,
        )
    ) == 1

    # 已有 trigram 版本的索引在初始化时升级为 jieba 词索引，并回填现有产物。
    with store.connect() as conn:
        for trigger in (
            "workflow_artifacts_search_content_ai",
            "workflow_artifacts_search_content_ad",
            "workflow_artifacts_search_content_au",
            "workflow_artifacts_search_content_base_ad",
            "workflow_artifacts_search_content_base_au",
        ):
            conn.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        conn.execute("DROP TABLE workflow_artifacts_search_content")
        conn.execute("DROP TABLE workflow_artifacts_fts")
        conn.execute(
            "UPDATE workflow_artifacts_fts_meta SET value = '1' WHERE key = 'index_version'"
        )
        conn.execute(
            """
            CREATE VIRTUAL TABLE workflow_artifacts_fts USING fts5(
              title, summary, path, content,
              content='workflow_artifacts',
              content_rowid='id',
              tokenize='trigram'
            )
            """
        )
        conn.execute("INSERT INTO workflow_artifacts_fts(workflow_artifacts_fts) VALUES ('rebuild')")
    store.init_schema()
    upgraded = store.search_workflow_artifacts(
        profile_key="report-plane",
        query="最懒工程师",
        tags=[],
        path=None,
        workflow_key=None,
        include_history=False,
        limit=10,
    )
    assert [item["artifact_id"] for item in upgraded] == [saved["artifact_id"]]

    with store.connect() as conn:
        conn.execute("DELETE FROM workflow_artifacts WHERE artifact_id = ?", (saved["artifact_id"],))
    store.init_schema()
    assert store.search_workflow_artifacts(
        profile_key="report-plane",
        query="revised_invoice_totals",
        tags=[],
        path=None,
        workflow_key=None,
        include_history=False,
        limit=10,
    ) == []


def test_workflow_artifacts_keep_same_version_outputs_for_different_runs(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    store.upsert_workflow_definition(
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        status="active",
        created_by="root",
    )

    first = store.upsert_workflow_artifact(
        workflow_key="page-report",
        profile_key="report-plane",
        run_id="run_1",
        task_key="page:a",
        task_version="v1",
        title="Page A v1 first",
        path="pages/page-a.md",
        tags=["finance"],
        format="markdown",
        summary="first",
        content="# first",
        metadata={},
    )
    second = store.upsert_workflow_artifact(
        workflow_key="page-report",
        profile_key="report-plane",
        run_id="run_2",
        task_key="page:a",
        task_version="v1",
        title="Page A v1 second",
        path="pages/page-a.md",
        tags=["finance"],
        format="markdown",
        summary="second",
        content="# second",
        metadata={},
    )

    assert first["artifact_id"] != second["artifact_id"]
    assert store.get_workflow_artifact(first["artifact_id"])["is_current"] is False
    assert store.get_workflow_artifact(second["artifact_id"])["is_current"] is True

    history = store.search_workflow_artifacts(
        profile_key="report-plane",
        query=None,
        tags=[],
        path="pages/page-a.md",
        workflow_key="page-report",
        task_key="page:a",
        task_version="v1",
        include_history=True,
        limit=10,
    )
    assert [(item["run_id"], item["content"]) for item in history] == [
        ("run_2", "# second"),
        ("run_1", "# first"),
    ]


def test_workflow_artifact_page_reuses_filters_for_items_and_total(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    store.upsert_workflow_definition(
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        status="active",
        created_by="root",
    )
    for index in range(3):
        store.upsert_workflow_artifact(
            workflow_key="page-report",
            profile_key="report-plane",
            run_id=f"run_{index}",
            task_key=f"page:{index}",
            title=f"Finance page {index}",
            path=f"pages/{index}.md",
            tags=["finance"],
            format="markdown",
            summary="finance summary",
            content="finance body",
            metadata={},
        )
    store.upsert_workflow_artifact(
        workflow_key="page-report",
        profile_key="report-plane",
        run_id="run_other",
        task_key="page:other",
        title="Other page",
        path="pages/other.md",
        tags=["other"],
        format="markdown",
        summary="other summary",
        content="other body",
        metadata={},
    )

    page = store.workflows.search_workflow_artifacts_page(
        profile_key="report-plane",
        query="finance",
        tags=["finance"],
        path="pages/",
        workflow_key="page-report",
        task_key=None,
        task_version=None,
        run_id=None,
        include_history=False,
        format=None,
        limit=1,
        offset=-10,
    )

    assert page["total"] == 3
    assert page["limit"] == 1
    assert page["offset"] == 0
    assert len(page["items"]) == 1


def test_workflow_migration_rebuilds_old_task_and_artifact_unique_constraints(wm_paths):
    from agent_bridge.storage.schema import CODEGRAPH_SCHEMA, SCHEMA
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    with store.connect() as conn:
        conn.executescript(SCHEMA)
        conn.executescript(CODEGRAPH_SCHEMA)
        conn.execute(
            """
            CREATE TABLE workflow_definitions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              workflow_key TEXT NOT NULL UNIQUE,
              name TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '',
              profile_key TEXT NOT NULL REFERENCES project_profiles(profile_key) ON DELETE RESTRICT,
              workflow_js TEXT NOT NULL DEFAULT '',
              manifest_json TEXT NOT NULL DEFAULT '{}',
              status TEXT NOT NULL DEFAULT 'active',
              created_by TEXT NOT NULL,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE workflow_tasks (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              workflow_key TEXT NOT NULL REFERENCES workflow_definitions(workflow_key) ON DELETE CASCADE,
              task_key TEXT NOT NULL,
              payload_json TEXT NOT NULL DEFAULT '{}',
              status TEXT NOT NULL DEFAULT 'pending',
              lease_run_id TEXT,
              lease_expires_at TEXT,
              attempt_count INTEGER NOT NULL DEFAULT 0,
              last_error TEXT,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              completed_at TEXT,
              UNIQUE (workflow_key, task_key)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE workflow_artifacts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              artifact_id TEXT NOT NULL UNIQUE,
              workflow_key TEXT NOT NULL REFERENCES workflow_definitions(workflow_key) ON DELETE CASCADE,
              profile_key TEXT NOT NULL,
              run_id TEXT NOT NULL,
              task_key TEXT,
              title TEXT NOT NULL,
              path TEXT NOT NULL,
              tags_json TEXT NOT NULL DEFAULT '[]',
              format TEXT NOT NULL DEFAULT 'markdown',
              summary TEXT NOT NULL DEFAULT '',
              content TEXT NOT NULL DEFAULT '',
              content_hash TEXT NOT NULL,
              metadata_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              UNIQUE (workflow_key, path)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO project_profiles (profile_key, name, created_by)
            VALUES ('report-plane', 'Report Plane', 'root')
            """
        )
        conn.execute(
            """
            INSERT INTO workflow_definitions (workflow_key, name, profile_key, created_by)
            VALUES ('legacy-report', 'Legacy Report', 'report-plane', 'root')
            """
        )
        conn.execute(
            """
            INSERT INTO workflow_tasks (workflow_key, task_key, payload_json, status)
            VALUES ('legacy-report', 'page:legacy', '{}', 'pending')
            """
        )

    store.init_schema()
    with store.connect() as conn:
        workflow_columns = {row["name"] for row in conn.execute("PRAGMA table_info(workflow_definitions)").fetchall()}
        assert "manifest_json" not in workflow_columns

    store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    store.upsert_workflow_definition(
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        status="active",
        created_by="root",
    )

    assert store.upsert_workflow_tasks(
        "page-report",
        [{"task_key": "page:a", "task_version": "v1", "payload": {}}],
    )["created"] == 1
    assert store.upsert_workflow_tasks(
        "page-report",
        [{"task_key": "page:a", "task_version": "v2", "payload": {}}],
    )["created"] == 1

    first = store.upsert_workflow_artifact(
        workflow_key="page-report",
        profile_key="report-plane",
        run_id="run_1",
        task_key="page:a",
        task_version="v1",
        title="Page A v1",
        path="pages/page-a.md",
        tags=[],
        format="markdown",
        summary="",
        content="# v1",
        metadata={},
    )
    second = store.upsert_workflow_artifact(
        workflow_key="page-report",
        profile_key="report-plane",
        run_id="run_2",
        task_key="page:a",
        task_version="v2",
        title="Page A v2",
        path="pages/page-a.md",
        tags=[],
        format="markdown",
        summary="",
        content="# v2",
        metadata={},
    )
    assert first["artifact_id"] != second["artifact_id"]


def test_workflow_concurrency_settings_default_and_persist(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()

    defaults = store.get_sync_config()
    assert defaults["workflow_max_concurrent_runs"] == 4
    assert defaults["workflow_max_concurrent_runs_per_workflow"] == 2

    saved = store.save_sync_config(
        code_sync_cron="0 * * * *",
        workflow_max_concurrent_runs=8,
        workflow_max_concurrent_runs_per_workflow=3,
    )

    assert saved["workflow_max_concurrent_runs"] == 8
    assert saved["workflow_max_concurrent_runs_per_workflow"] == 3
    assert store.get_sync_config()["workflow_max_concurrent_runs"] == 8
    assert store.get_sync_config()["workflow_max_concurrent_runs_per_workflow"] == 3
