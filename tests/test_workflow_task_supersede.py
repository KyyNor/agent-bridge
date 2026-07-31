"""task_version 演进模型测试：新版本到来时取代旧版本，跨版本禁止增量复用。

覆盖范围：
- 新版本到来时，尚未运行的旧版本（pending/stale）被取代为 superseded。
- 正在运行的旧版本（running，含租约未过期）不被取代，让它跑完。
- 已成功完成的旧版本不被取代，保留为历史产物。
- 已失败/放弃的旧版本同样被取代（无需重试）。
- 调度器永不领取 superseded 任务。
- 自动迁移把存量"同 task_key 多 version 排队"的旧版本回填为 superseded，且幂等。
- 跨版本禁止增量复用（baseline 必须 task_version 硬等值）。
"""

from __future__ import annotations

from pathlib import Path

from agent_bridge.automation.workflows.models import WorkflowTaskStatus


def _fresh_store(wm_paths):
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


def _status(store, task_key, task_version):
    return store.get_workflow_task("page-report", task_key, task_version=task_version)["status"]


# ---------------------------------------------------------------------------
# 新版本取代尚未运行的旧版本
# ---------------------------------------------------------------------------

def test_new_version_supersedes_pending_old_version(wm_paths):
    store = _fresh_store(wm_paths)
    store.upsert_workflow_tasks("page-report", [{"task_key": "page:a", "task_version": "v1", "payload": {}}])
    assert _status(store, "page:a", "v1") == WorkflowTaskStatus.pending.value

    result = store.upsert_workflow_tasks(
        "page-report", [{"task_key": "page:a", "task_version": "v2", "payload": {}}]
    )

    assert _status(store, "page:a", "v1") == WorkflowTaskStatus.superseded.value
    assert _status(store, "page:a", "v2") == WorkflowTaskStatus.pending.value
    # 旧版本租约被清空，避免残留。
    v1 = store.get_workflow_task("page-report", "page:a", task_version="v1")
    assert v1["lease_run_id"] is None
    assert v1["lease_expires_at"] is None
    # 计数反映取代。
    assert result["created"] == 1
    assert result["superseded"] == 1


def test_new_version_supersedes_stale_old_version(wm_paths):
    store = _fresh_store(wm_paths)
    store.upsert_workflow_tasks("page-report", [{"task_key": "page:a", "task_version": "v1", "payload": {}}])
    # 把 v1 推进到 stale（通过 release_tasks_for_revision_mismatch 等价手段直接置位）。
    with store.connect() as conn:
        conn.execute(
            "UPDATE workflow_tasks SET status = 'stale' WHERE workflow_key = 'page-report' AND task_key = 'page:a'"
        )

    store.upsert_workflow_tasks("page-report", [{"task_key": "page:a", "task_version": "v2", "payload": {}}])

    assert _status(store, "page:a", "v1") == WorkflowTaskStatus.superseded.value
    assert _status(store, "page:a", "v2") == WorkflowTaskStatus.pending.value


def test_new_version_supersedes_failed_and_abandoned_old_versions(wm_paths):
    """失败/放弃的旧版本同样被取代：无需继续重试旧版本。"""
    store = _fresh_store(wm_paths)
    store.upsert_workflow_tasks(
        "page-report",
        [
            {"task_key": "page:a", "task_version": "v1", "payload": {}},
            {"task_key": "page:b", "task_version": "v1", "payload": {}},
        ],
    )
    with store.connect() as conn:
        conn.execute(
            "UPDATE workflow_tasks SET status = 'failed' "
            "WHERE workflow_key = 'page-report' AND task_key = 'page:a'"
        )
        conn.execute(
            "UPDATE workflow_tasks SET status = 'abandoned' "
            "WHERE workflow_key = 'page-report' AND task_key = 'page:b'"
        )

    store.upsert_workflow_tasks(
        "page-report",
        [
            {"task_key": "page:a", "task_version": "v2", "payload": {}},
            {"task_key": "page:b", "task_version": "v2", "payload": {}},
        ],
    )

    assert _status(store, "page:a", "v1") == WorkflowTaskStatus.superseded.value
    assert _status(store, "page:b", "v1") == WorkflowTaskStatus.superseded.value


def test_running_old_version_is_not_superseded(wm_paths):
    """正在运行的旧版本不被取代，让它跑完。"""
    store = _fresh_store(wm_paths)
    store.upsert_workflow_tasks("page-report", [{"task_key": "page:a", "task_version": "v1", "payload": {}}])
    store.create_workflow_run(
        run_id="run-v1",
        workflow_key="page-report",
        profile_key="report-plane",
        task_key="page:a",
        task_version="v1",
        status="running",
        temp_dir="/tmp/run-v1",
    )
    leased = store.lease_workflow_task("page-report", run_id="run-v1", lease_seconds=7200)
    assert leased["task_version"] == "v1"

    result = store.upsert_workflow_tasks(
        "page-report", [{"task_key": "page:a", "task_version": "v2", "payload": {}}]
    )

    # v1 仍在运行，不被取代；v2 进入队列待执行。
    assert _status(store, "page:a", "v1") == WorkflowTaskStatus.running.value
    assert _status(store, "page:a", "v2") == WorkflowTaskStatus.pending.value
    assert result["superseded"] == 0


def test_completed_old_version_is_not_superseded(wm_paths):
    """已成功完成的旧版本保留为历史产物，不被取代。"""
    store = _fresh_store(wm_paths)
    store.upsert_workflow_tasks("page-report", [{"task_key": "page:a", "task_version": "v1", "payload": {}}])
    store.create_workflow_run(
        run_id="run-v1",
        workflow_key="page-report",
        profile_key="report-plane",
        task_key="page:a",
        task_version="v1",
        status="running",
        temp_dir="/tmp/run-v1",
    )
    store.lease_workflow_task("page-report", run_id="run-v1", lease_seconds=7200)
    store.complete_workflow_task("page-report", "page:a", task_version="v1", run_id="run-v1")

    store.upsert_workflow_tasks("page-report", [{"task_key": "page:a", "task_version": "v2", "payload": {}}])

    assert _status(store, "page:a", "v1") == WorkflowTaskStatus.completed.value
    assert _status(store, "page:a", "v2") == WorkflowTaskStatus.pending.value


# ---------------------------------------------------------------------------
# 调度器永不领取 superseded
# ---------------------------------------------------------------------------

def test_scheduler_never_leases_superseded_task(wm_paths):
    store = _fresh_store(wm_paths)
    store.upsert_workflow_tasks("page-report", [{"task_key": "page:a", "task_version": "v1", "payload": {}}])
    store.upsert_workflow_tasks("page-report", [{"task_key": "page:a", "task_version": "v2", "payload": {}}])

    # v1 已被取代，只有 v2 可被领取。
    leased = store.lease_workflow_task("page-report", run_id="run-1", lease_seconds=7200)
    assert leased is not None
    assert leased["task_version"] == "v2"

    # v2 领走后，队列里虽仍有 superseded 的 v1，但不应再被领取。
    assert store.lease_workflow_task("page-report", run_id="run-2", lease_seconds=7200) is None


def test_list_workflow_tasks_excludes_superseded_by_default(wm_paths):
    store = _fresh_store(wm_paths)
    store.upsert_workflow_tasks("page-report", [{"task_key": "page:a", "task_version": "v1", "payload": {}}])
    store.upsert_workflow_tasks("page-report", [{"task_key": "page:a", "task_version": "v2", "payload": {}}])

    listed = store.list_workflow_tasks("page-report")
    versions = [t["task_version"] for t in listed]
    assert versions == ["v2"]  # superseded 的 v1 默认不出现

    # 显式按 status 查询仍能看到 superseded。
    historical = store.list_workflow_tasks("page-report", status="superseded")
    assert [t["task_version"] for t in historical] == ["v1"]


# ---------------------------------------------------------------------------
# 预览返回取代计数
# ---------------------------------------------------------------------------

def test_preview_reports_superseded_count(wm_paths):
    store = _fresh_store(wm_paths)
    store.upsert_workflow_tasks(
        "page-report",
        [
            {"task_key": "page:a", "task_version": "v1", "payload": {}},
            {"task_key": "page:b", "task_version": "v1", "payload": {}},
        ],
    )

    preview = store.preview_workflow_task_actions(
        "page-report",
        [{"task_key": "page:a", "task_version": "v2", "payload": {}}],
    )

    assert preview["summary"]["created"] == 1
    assert preview["summary"]["superseded"] == 1  # v1(a) 将被取代
    # 预览不写库，v1 仍是 pending。
    assert _status(store, "page:a", "v1") == WorkflowTaskStatus.pending.value


# ---------------------------------------------------------------------------
# 自动迁移：存量多 version 排队 → superseded，且幂等
# ---------------------------------------------------------------------------

def test_backfill_supersedes_non_latest_pending_versions(tmp_path: Path):
    from agent_bridge.storage.migrations.workflows import backfill_workflow_tasks_superseded
    from agent_bridge.storage.sqlite import SQLiteStore

    # 直接构造一个最小库，不走 wm_paths 以便控制 set_at 顺序。
    db = tmp_path / "backfill.db"
    store = SQLiteStore(db)
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
    # 直接插行，模拟旧模型遗留的“同 task_key 多 version 都在 pending”。
    # 注意：v2 的 set_at 晚于 v1（与运行时 current 判定一致）。
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO workflow_tasks (workflow_key, task_key, task_version, type, payload_json, status, set_at) "
            "VALUES ('page-report','page:a','v1','','{}','pending','2026-01-01T00:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO workflow_tasks (workflow_key, task_key, task_version, type, payload_json, status, set_at) "
            "VALUES ('page-report','page:a','v2','','{}','pending','2026-02-01T00:00:00+00:00')"
        )
        # 另一个 task_key 只有一个 version，应保留 pending。
        conn.execute(
            "INSERT INTO workflow_tasks (workflow_key, task_key, task_version, type, payload_json, status, set_at) "
            "VALUES ('page-report','page:b','v1','','{}','pending','2026-01-01T00:00:00+00:00')"
        )
        # 一个已完成的旧 version 不应被迁移触碰（保留为历史）。
        conn.execute(
            "INSERT INTO workflow_tasks (workflow_key, task_key, task_version, type, payload_json, status, set_at) "
            "VALUES ('page-report','page:c','v1','','{}','completed','2026-01-01T00:00:00+00:00')"
        )
        # init_schema 已在空库时跑过一次迁移并写入版本号；这里清除标记以模拟
        # “首次升级存量库”的场景，从而直接验证迁移逻辑本身。
        conn.execute(
            "DELETE FROM workflow_artifacts_fts_meta WHERE key = 'tasks_superseded_backfill'"
        )
        conn.commit()

    with store.connect() as conn:
        backfill_workflow_tasks_superseded(conn)
        conn.commit()

    with store.connect() as conn:
        rows = {
            (r["task_key"], r["task_version"]): r["status"]
            for r in conn.execute("SELECT task_key, task_version, status FROM workflow_tasks")
        }
    # page:a 的非最新 version(v1) 被取代；v2 保留为 pending。
    assert rows[("page:a", "v1")] == WorkflowTaskStatus.superseded.value
    assert rows[("page:a", "v2")] == WorkflowTaskStatus.pending.value
    # 单 version 的 page:b 保留 pending。
    assert rows[("page:b", "v1")] == WorkflowTaskStatus.pending.value
    # 已完成的 page:c 不被迁移触碰。
    assert rows[("page:c", "v1")] == WorkflowTaskStatus.completed.value

    # 幂等：再跑一次，状态不变，且不重复处理。
    with store.connect() as conn:
        backfill_workflow_tasks_superseded(conn)
        conn.commit()
    with store.connect() as conn:
        rows2 = {
            (r["task_key"], r["task_version"]): r["status"]
            for r in conn.execute("SELECT task_key, task_version, status FROM workflow_tasks")
        }
    assert rows2 == rows


def test_backfill_is_idempotent_across_schema_init(tmp_path: Path):
    """init_schema 内部已调用迁移；多次 init 不应重复改写或报错。"""
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(tmp_path / "idem.db")
    store.init_schema()
    store.upsert_project_profile(profile_key="p", name="p", created_by="root")
    store.upsert_workflow_definition(
        workflow_key="w", name="w", description="", profile_key="p", status="active", created_by="root"
    )
    store.upsert_workflow_tasks("w", [{"task_key": "a", "task_version": "v1", "payload": {}}])
    store.upsert_workflow_tasks("w", [{"task_key": "a", "task_version": "v2", "payload": {}}])
    before = store.get_workflow_task("w", "a", task_version="v1")["status"]

    # 再次 init_schema（例如应用重启）不应改变既有状态。
    store.init_schema()
    after = store.get_workflow_task("w", "a", task_version="v1")["status"]
    assert before == after


# ---------------------------------------------------------------------------
# 跨版本禁止增量复用
# ---------------------------------------------------------------------------

def test_select_baseline_requires_matching_task_version(wm_paths):
    from agent_bridge.automation.workflows.incremental import WorkflowIncrementalPlanner

    planner = WorkflowIncrementalPlanner()
    baseline_v1 = {
        "status": "completed",
        "workflow_key": "page-report",
        "profile_key": "report-plane",
        "task_key": "page:a",
        "task_version": "v1",
        "id": 1,
        "finished_at": "2026-01-01T00:00:00+00:00",
    }

    # v2 的任务不应复用 v1 的 baseline（跨版本报表内容不同，解析结构不能复用）。
    selected = planner.select_baseline(
        baseline_run=[baseline_v1],
        workflow_key="page-report",
        profile_key="report-plane",
        task_key="page:a",
        task_version="v2",
    )
    assert selected is None

    # 同版本仍可复用。
    same = planner.select_baseline(
        baseline_run=[baseline_v1],
        workflow_key="page-report",
        profile_key="report-plane",
        task_key="page:a",
        task_version="v1",
    )
    assert same is not None
    assert same["task_version"] == "v1"


# ---------------------------------------------------------------------------
# 单任务入口（workflow_set_task）共用同一取代逻辑
# ---------------------------------------------------------------------------

def test_workflow_set_task_path_triggers_supersede_via_upsert(wm_paths):
    """单任务/批量入口最终都汇到 upsert_workflow_tasks，取代行为一致。"""
    store = _fresh_store(wm_paths)
    store.upsert_workflow_tasks("page-report", [{"task_key": "page:a", "task_version": "v1", "payload": {}}])

    # 模拟单发入口：upsert 单条新 version。
    result = store.upsert_workflow_tasks(
        "page-report", [{"task_key": "page:a", "task_version": "v2", "payload": {"k": "v"}}]
    )

    assert _status(store, "page:a", "v1") == WorkflowTaskStatus.superseded.value
    assert _status(store, "page:a", "v2") == WorkflowTaskStatus.pending.value
    assert result["superseded"] == 1
