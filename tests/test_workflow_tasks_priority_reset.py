"""Priority flag (execute) + reset for workflow tasks (features 3 & 4, store layer)."""

from __future__ import annotations

import pytest


def _fresh_store(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    return store


@pytest.fixture
def store_two_tasks(wm_paths):
    store = _fresh_store(wm_paths)
    _seed_two_tasks(store)
    return store


@pytest.fixture
def store_with_versioned_task(wm_paths):
    store = _fresh_store(wm_paths)
    _seed(store)
    store.upsert_workflow_tasks(
        "w",
        [
            {"task_key": "page:a", "task_version": "v1", "payload": {}},
            {"task_key": "page:a", "task_version": "v2", "payload": {}},
        ],
    )
    return store


@pytest.fixture
def store_priority_on_completed(wm_paths):
    store = _fresh_store(wm_paths)
    _seed_two_tasks(store)
    # Lease + complete page:alpha (run_1). page:beta stays pending.
    store.lease_workflow_task("w", run_id="run_1", lease_seconds=7200)
    store.complete_workflow_task("w", "page:alpha", run_id="run_1")
    # Flag the *completed* alpha — the flag must not resurrect it.
    store.set_priority_for_task("w", "page:alpha")
    return store


@pytest.fixture
def store_abandoned(wm_paths):
    store = _fresh_store(wm_paths)
    _seed(store)
    store.upsert_workflow_tasks("w", [{"task_key": "page:a", "payload": {}}])
    store.create_workflow_run(
        run_id="run_1",
        workflow_key="w",
        profile_key="report-plane",
        task_key=None,
        status="running",
        temp_dir="/tmp/run_1",
    )
    store.lease_workflow_task("w", run_id="run_1", lease_seconds=7200)
    store.release_or_abandon_tasks_for_run("w", "run_1", max_attempts=0, error_message="boom")
    assert store.get_workflow_task("w", "page:a")["status"] == "abandoned"
    return store


@pytest.fixture
def store_versioned_abandoned(wm_paths):
    store = _fresh_store(wm_paths)
    _seed(store)
    # 两个独立 task_key 各自只有一个 version；新版本演进语义下，
    # 同 task_key 的未运行旧 version 会被取代，无法再用作共存样本，
    # 因此这里用不同 task_key 验证 reset 按 task_version 精确定位。
    store.upsert_workflow_tasks(
        "w",
        [
            {"task_key": "page:a", "task_version": "v1", "payload": {}},
            {"task_key": "page:b", "task_version": "v1", "payload": {}},
        ],
    )
    store.create_workflow_run(
        run_id="run_1",
        workflow_key="w",
        profile_key="report-plane",
        task_key=None,
        status="running",
        temp_dir="/tmp/run_1",
    )
    # Lease & abandon page:a (lower id is picked first). page:b remains pending.
    store.lease_workflow_task("w", run_id="run_1", lease_seconds=7200)
    leased_a = store.get_workflow_task("w", "page:a", task_version="v1")
    assert leased_a["lease_run_id"] == "run_1"
    store.release_or_abandon_tasks_for_run("w", "run_1", max_attempts=0, error_message="boom")
    assert store.get_workflow_task("w", "page:a", task_version="v1")["status"] == "abandoned"
    assert store.get_workflow_task("w", "page:b", task_version="v1")["status"] == "pending"
    return store


@pytest.fixture
def store_empty_workflow(wm_paths):
    store = _fresh_store(wm_paths)
    _seed(store)
    return store


def _seed(store, workflow_key: str = "w"):
    store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    store.upsert_workflow_definition(
        workflow_key=workflow_key,
        name=workflow_key,
        description="",
        profile_key="report-plane",
        status="active",
        created_by="root",
    )


def _seed_two_tasks(store, workflow_key: str = "w"):
    """page:alpha (id lower) and page:beta (id higher), both pending."""
    _seed(store, workflow_key)
    store.upsert_workflow_tasks(
        workflow_key,
        [
            {"task_key": "page:alpha", "payload": {}},
            {"task_key": "page:beta", "payload": {}},
        ],
    )


# ---------------------------------------------------------------------------
# set_priority_for_task
# ---------------------------------------------------------------------------

def test_set_priority_marks_task_with_timestamp(store_two_tasks):
    store = store_two_tasks
    store.set_priority_for_task("w", "page:beta")

    alpha = store.get_workflow_task("w", "page:alpha")
    beta = store.get_workflow_task("w", "page:beta")
    assert alpha["priority_flag"] is None
    assert beta["priority_flag"] is not None  # an ISO timestamp string


def test_set_priority_supports_task_version(store_with_versioned_task):
    store = store_with_versioned_task
    store.set_priority_for_task("w", "page:a", task_version="v2")

    v1 = store.get_workflow_task("w", "page:a", task_version="v1")
    v2 = store.get_workflow_task("w", "page:a", task_version="v2")
    assert v1["priority_flag"] is None
    assert v2["priority_flag"] is not None


# ---------------------------------------------------------------------------
# lease_workflow_task honours priority_flag
# ---------------------------------------------------------------------------

def test_lease_prefers_priority_flagged_task_over_lower_id(store_two_tasks):
    store = store_two_tasks
    # beta has the higher id; without a flag alpha (lower id) would win.
    store.set_priority_for_task("w", "page:beta")

    leased = store.lease_workflow_task("w", run_id="run_1", lease_seconds=7200)
    assert leased["task_key"] == "page:beta"


def test_lease_clears_priority_flag_when_taken(store_two_tasks):
    """A flagged task consumed by a lease must not keep its flag, so the next
    run falls back to normal id ordering (one-shot priority)."""
    store = store_two_tasks
    store.set_priority_for_task("w", "page:beta")
    leased = store.lease_workflow_task("w", run_id="run_1", lease_seconds=7200)
    assert leased["task_key"] == "page:beta"

    assert store.get_workflow_task("w", "page:beta")["priority_flag"] is None


def test_lease_without_any_flag_keeps_id_order(store_two_tasks):
    store = store_two_tasks
    leased = store.lease_workflow_task("w", run_id="run_1", lease_seconds=7200)
    assert leased["task_key"] == "page:alpha"


def test_lease_priority_only_applies_to_leasable_task(store_priority_on_completed):
    """A flag on a non-leasable (completed) task must not surface it; lease
    picks the next eligible task instead."""
    store = store_priority_on_completed
    leased = store.lease_workflow_task("w", run_id="run_2", lease_seconds=7200)
    # alpha is completed (hence not eligible) even though flagged; the pending
    # beta is the only leasable task, so it wins.
    assert leased["task_key"] == "page:beta"


# ---------------------------------------------------------------------------
# reset_workflow_task
# ---------------------------------------------------------------------------

def test_reset_makes_abandoned_task_leasable_again(store_abandoned):
    store = store_abandoned
    task = store.get_workflow_task("w", "page:a")
    assert task["status"] == "abandoned"

    store.reset_workflow_task("w", "page:a")

    refreshed = store.get_workflow_task("w", "page:a")
    assert refreshed["status"] == "pending"
    assert refreshed["lease_run_id"] is None
    assert refreshed["lease_expires_at"] is None
    assert refreshed["completed_at"] is None
    assert refreshed["priority_flag"] is None

    # And it can now be leased again.
    leased = store.lease_workflow_task("w", run_id="run_new", lease_seconds=7200)
    assert leased["task_key"] == "page:a"


def test_reset_preserves_attempt_count_and_last_error(store_abandoned):
    """Reset restores leasability but keeps the audit trail (attempts, error)."""
    store = store_abandoned
    before = store.get_workflow_task("w", "page:a")
    assert before["attempt_count"] >= 1
    assert before["last_error"]

    store.reset_workflow_task("w", "page:a")

    after = store.get_workflow_task("w", "page:a")
    assert after["attempt_count"] == before["attempt_count"]
    assert after["last_error"] == before["last_error"]


def test_successful_retry_clears_last_error(store_abandoned):
    """成功重试后，任务列表不应继续展示上一次失败原因。"""
    store = store_abandoned
    store.reset_workflow_task("w", "page:a")
    store.create_workflow_run(
        run_id="run_new",
        workflow_key="w",
        profile_key="report-plane",
        task_key=None,
        status="running",
        temp_dir="/tmp/run_new",
    )
    leased = store.lease_workflow_task("w", run_id="run_new", lease_seconds=7200)
    assert leased is not None
    assert store.complete_workflow_task("w", "page:a", run_id="run_new") is True

    task = store.get_workflow_task("w", "page:a")
    assert task["status"] == "completed"
    assert task["last_error"] is None


def test_reset_does_not_trigger_execution(store_abandoned):
    """Reset only flips status; it must not create or start a new run."""
    store = store_abandoned
    runs_before = len(store.list_workflow_runs("w", limit=10))
    store.reset_workflow_task("w", "page:a")
    runs_after = len(store.list_workflow_runs("w", limit=10))
    assert runs_after == runs_before  # no new run created by reset


def test_reset_missing_task_returns_false(store_empty_workflow):
    store = store_empty_workflow
    assert store.reset_workflow_task("w", "nope") is False


def test_reset_supports_task_version(store_versioned_abandoned):
    store = store_versioned_abandoned
    assert store.reset_workflow_task("w", "page:a", task_version="v1") is True
    assert store.get_workflow_task("w", "page:a", task_version="v1")["status"] == "pending"
    # The other task is untouched (it was never abandoned).
    assert store.get_workflow_task("w", "page:b", task_version="v1")["status"] == "pending"
