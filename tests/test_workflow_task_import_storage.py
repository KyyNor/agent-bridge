from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


TASK_ACTIONS = {
    "created": 0,
    "updated": 0,
    "skipped_completed": 0,
    "skipped_running": 0,
    "skipped_historical": 0,
    "reopened_expired": 0,
}


def action_counts(**overrides):
    result = dict(TASK_ACTIONS)
    result.update(overrides)
    return result


def _seed_workflow(wm_paths):
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
    return store


def _create_import(store, *, import_id="import-1", actor="alice", tasks=None, preview=None, expires_at=None):
    tasks = tasks or []
    preview = preview or {"rows": [], "summary": action_counts()}
    expires_at = expires_at or (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    return store.create_workflow_task_import(
        import_id=import_id,
        workflow_key="page-report",
        actor=actor,
        filename="tasks.xlsx",
        sheet_name="tasks",
        tasks=tasks,
        preview=preview,
        expires_at=expires_at,
    )


def test_preview_workflow_task_actions_classifies_created_and_updated(wm_paths):
    store = _seed_workflow(wm_paths)
    store.upsert_workflow_tasks(
        "page-report",
        [{"task_key": "task:a", "payload": {"v": 1}}],
    )

    preview = store.preview_workflow_task_actions(
        "page-report",
        [
            {"task_key": "task:a", "payload": {"v": 2}},
            {"task_key": "task:new", "payload": {"v": 3}},
        ],
    )

    assert [row["action"] for row in preview["rows"]] == ["updated", "created"]
    assert preview["summary"] == action_counts(updated=1, created=1)


def test_confirm_workflow_task_import_creates_tasks_and_marks_snapshot_confirmed(wm_paths):
    store = _seed_workflow(wm_paths)
    tasks = [{"task_key": "task:new", "task_version": "v1", "type": "repo", "payload": {"v": 3}}]
    preview = store.preview_workflow_task_actions("page-report", tasks)
    _create_import(store, tasks=tasks, preview=preview)

    result = store.confirm_workflow_task_import(
        "page-report",
        import_id="import-1",
        actor="alice",
    )

    assert result == {"import_id": "import-1", **action_counts(created=1)}
    snapshot = store.get_workflow_task_import("import-1")
    assert snapshot["status"] == "confirmed"
    assert snapshot["confirmed_at"] is not None
    assert snapshot["tasks"] == tasks
    assert store.get_workflow_task("page-report", "task:new", task_version="v1")["payload"] == {"v": 3}

    with pytest.raises(ValueError, match="not previewed"):
        store.confirm_workflow_task_import("page-report", import_id="import-1", actor="alice")


def test_expired_workflow_task_import_is_rejected_without_writing_tasks(wm_paths):
    store = _seed_workflow(wm_paths)
    tasks = [{"task_key": "task:expired", "payload": {"v": 1}}]
    _create_import(
        store,
        tasks=tasks,
        expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
    )

    with pytest.raises(ValueError, match="expired"):
        store.confirm_workflow_task_import("page-report", import_id="import-1", actor="alice")

    assert store.get_workflow_task("page-report", "task:expired") is None
    assert store.get_workflow_task_import("import-1")["status"] == "previewed"


def test_different_actor_cannot_confirm_workflow_task_import(wm_paths):
    store = _seed_workflow(wm_paths)
    tasks = [{"task_key": "task:actor", "payload": {"v": 1}}]
    preview = store.preview_workflow_task_actions("page-report", tasks)
    _create_import(store, actor="alice", tasks=tasks, preview=preview)

    with pytest.raises(ValueError, match="actor"):
        store.confirm_workflow_task_import("page-report", import_id="import-1", actor="bob")

    assert store.get_workflow_task("page-report", "task:actor") is None
    assert store.get_workflow_task_import("import-1")["status"] == "previewed"


def test_confirmation_rechecks_running_lease_and_skips_task(wm_paths):
    store = _seed_workflow(wm_paths)
    store.upsert_workflow_tasks(
        "page-report",
        [{"task_key": "task:running", "payload": {"v": 1}}],
    )
    tasks = [{"task_key": "task:running", "payload": {"v": 2}}]
    preview = store.preview_workflow_task_actions("page-report", tasks)
    assert preview["rows"][0]["action"] == "updated"
    _create_import(store, tasks=tasks, preview=preview)

    leased = store.lease_workflow_task("page-report", run_id="run-1", lease_seconds=7200)
    assert leased is not None
    result = store.confirm_workflow_task_import("page-report", import_id="import-1", actor="alice")

    assert result == {"import_id": "import-1", **action_counts(skipped_running=1)}
    task = store.get_workflow_task("page-report", "task:running")
    assert task["payload"] == {"v": 1}
    assert task["lease_run_id"] == "run-1"


def test_confirmation_rolls_back_task_writes_and_snapshot_status_on_error(wm_paths):
    store = _seed_workflow(wm_paths)
    tasks = [
        {"task_key": "task:first", "payload": {"v": 1}},
        {"payload": {"v": 2}},
    ]
    _create_import(store, tasks=tasks)

    with pytest.raises(KeyError):
        store.confirm_workflow_task_import("page-report", import_id="import-1", actor="alice")

    assert store.get_workflow_task("page-report", "task:first") is None
    assert store.get_workflow_task_import("import-1")["status"] == "previewed"


def test_delete_expired_workflow_task_imports_removes_only_expired_previews(wm_paths):
    store = _seed_workflow(wm_paths)
    _create_import(
        store,
        import_id="expired",
        expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
    )
    _create_import(store, import_id="active")

    assert store.delete_expired_workflow_task_imports() == 1
    assert store.get_workflow_task_import("expired") is None
    assert store.get_workflow_task_import("active") is not None


def test_clear_workflow_execution_data_deletes_pending_import_snapshots(wm_paths):
    store = _seed_workflow(wm_paths)
    _create_import(store, import_id="pending")
    tasks = [{"task_key": "task:confirmed", "payload": {"v": 1}}]
    confirmed_preview = store.preview_workflow_task_actions("page-report", tasks)
    _create_import(store, import_id="confirmed", tasks=tasks, preview=confirmed_preview)
    store.confirm_workflow_task_import("page-report", import_id="confirmed", actor="alice")

    assert store.clear_workflow_execution_data("page-report") == {
        "tasks_deleted": 1,
        "runs_deleted": 0,
        "logs_deleted": 0,
        "artifacts_deleted": 0,
    }
    assert store.get_workflow_task_import("pending") is None
    assert store.get_workflow_task_import("confirmed")["status"] == "confirmed"
