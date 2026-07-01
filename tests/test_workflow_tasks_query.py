"""Filtering / searching / sorting for workflow task listings (feature 1)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _seed(store) -> None:
    store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    store.upsert_workflow_definition(
        workflow_key="w",
        name="w",
        description="",
        profile_key="report-plane",
        workflow_js="",
        status="active",
        created_by="root",
    )
    # Mixed task_keys/types so filtering & search are observable.
    store.upsert_workflow_tasks(
        "w",
        [
            {"task_key": "page:alpha", "type": "report", "payload": {}},
            {"task_key": "page:beta", "type": "report", "payload": {}},
            {"task_key": "page:gamma", "type": "index", "payload": {}},
        ],
    )


def _task_keys(tasks):
    return [t["task_key"] for t in tasks]


def test_list_tasks_default_sort_is_status_priority_then_recency(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    _seed(store)

    # Without filters the order must match the historical default
    # (status priority, then updated_at DESC, then id DESC). Here every task is
    # pending, inserted in id order; default returns them newest-id-first.
    default = store.list_workflow_tasks("w")
    assert _task_keys(default) == ["page:gamma", "page:beta", "page:alpha"]


def test_list_tasks_filter_by_status(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    _seed(store)
    # Complete one task so a status filter has something to match.
    store.lease_workflow_task("w", run_id="run_1", lease_seconds=7200)
    store.complete_workflow_task("w", "page:alpha", run_id="run_1")

    completed = store.list_workflow_tasks("w", status="completed")
    assert _task_keys(completed) == ["page:alpha"]

    pending = store.list_workflow_tasks("w", status="pending")
    assert _task_keys(pending) == ["page:gamma", "page:beta"]


def test_list_tasks_filter_by_type(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    _seed(store)

    reports = store.list_workflow_tasks("w", type="report")
    assert _task_keys(reports) == ["page:beta", "page:alpha"]

    indexes = store.list_workflow_tasks("w", type="index")
    assert _task_keys(indexes) == ["page:gamma"]


def test_list_tasks_search_matches_task_key_substring(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    _seed(store)

    hits = store.list_workflow_tasks("w", search="alpha")
    assert _task_keys(hits) == ["page:alpha"]

    # Substring across several keys, case-insensitive.
    hits = store.list_workflow_tasks("w", search="PAGE")
    assert _task_keys(hits) == ["page:gamma", "page:beta", "page:alpha"]


def test_list_tasks_search_matches_type_substring(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    _seed(store)

    hits = store.list_workflow_tasks("w", search="index")
    assert _task_keys(hits) == ["page:gamma"]


def test_list_tasks_sort_options(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    _seed(store)

    # id ascending -> insertion order.
    assert _task_keys(store.list_workflow_tasks("w", sort="id_asc")) == [
        "page:alpha",
        "page:beta",
        "page:gamma",
    ]
    # id descending -> reverse insertion order (same as default when all equal).
    assert _task_keys(store.list_workflow_tasks("w", sort="id_desc")) == [
        "page:gamma",
        "page:beta",
        "page:alpha",
    ]

    # set_at asc / desc reflect insertion order too (set_at defaults to now).
    assert _task_keys(store.list_workflow_tasks("w", sort="set_at_asc")) == [
        "page:alpha",
        "page:beta",
        "page:gamma",
    ]
    assert _task_keys(store.list_workflow_tasks("w", sort="set_at_desc")) == [
        "page:gamma",
        "page:beta",
        "page:alpha",
    ]


def test_list_tasks_sort_set_at_asc_honours_backdated_rows(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    _seed(store)

    # Force gamma to be the oldest by set_at, so ascending puts it first even
    # though it has the highest id.
    old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    with store.connect() as conn:
        conn.execute(
            "UPDATE workflow_tasks SET set_at = ? WHERE task_key = ?",
            (old, "page:gamma"),
        )

    assert _task_keys(store.list_workflow_tasks("w", sort="set_at_asc"))[0] == "page:gamma"


def test_list_tasks_unknown_sort_falls_back_to_default(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    _seed(store)

    # An unrecognised sort value must not crash and must behave like default.
    assert _task_keys(store.list_workflow_tasks("w", sort="bogus")) == [
        "page:gamma",
        "page:beta",
        "page:alpha",
    ]


def test_list_tasks_combined_filter_and_sort(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    _seed(store)

    # Only reports, sorted by id ascending.
    hits = store.list_workflow_tasks("w", type="report", sort="id_asc")
    assert _task_keys(hits) == ["page:alpha", "page:beta"]
