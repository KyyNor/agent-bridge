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

    # task_key ascending / descending use lexical key order.
    assert _task_keys(store.list_workflow_tasks("w", sort="task_key_asc")) == [
        "page:alpha",
        "page:beta",
        "page:gamma",
    ]
    assert _task_keys(store.list_workflow_tasks("w", sort="task_key_desc")) == [
        "page:gamma",
        "page:beta",
        "page:alpha",
    ]

    # id ascending -> insertion order (kept as a compatibility alias).
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


def test_list_tasks_carries_has_artifacts_flag(wm_paths):
    """每行任务按 task_key 聚合所有版本的产物，带 has_artifacts 派生字段。"""
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    _seed(store)

    # 给 alpha 写一条产物；run_id 不存在的 run 默认视为 completed，is_current=1。
    store.upsert_workflow_artifact(
        workflow_key="w",
        profile_key="report-plane",
        run_id="run_alpha",
        task_key="page:alpha",
        task_version="",
        title="alpha 报告",
        path="alpha.md",
        tags=[],
        format="markdown",
        summary="",
        content="alpha",
        metadata={},
    )

    tasks = store.list_workflow_tasks("w")
    by_key = {t["task_key"]: t for t in tasks}
    assert by_key["page:alpha"]["has_artifacts"] is True
    assert by_key["page:beta"]["has_artifacts"] is False
    assert by_key["page:gamma"]["has_artifacts"] is False


def test_list_tasks_has_artifacts_includes_history_versions(wm_paths):
    """当前任务版本没有产物时，历史版本产物仍应使任务归入有产物。"""
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    _seed(store)

    # 为 alpha 的历史 v2 写产物，但当前队列任务仍是默认空版本（''）。
    store.upsert_workflow_artifact(
        workflow_key="w",
        profile_key="report-plane",
        run_id="run_alpha_v2",
        task_key="page:alpha",
        task_version="v2",
        title="alpha v2 报告",
        path="alpha-v2.md",
        tags=[],
        format="markdown",
        summary="",
        content="alpha v2",
        metadata={},
    )

    tasks = store.list_workflow_tasks("w")
    alpha = next(t for t in tasks if t["task_key"] == "page:alpha")
    assert alpha["task_version"] == ""
    assert alpha["has_artifacts"] is True
