"""统一数据生命周期治理（issue #12）的清理窗口、目录回收与首启迁移测试。"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_bridge.app.service import AgentBridgeService
from agent_bridge.capability_hub.models import CallLogStatus
from agent_bridge.core.domain import ValidationError
from agent_bridge.system_config.data_retention import (
    RETENTION_V1_CLEANUP_MARKER,
    RETENTION_V1_LEDGER_VACUUM_MARKER,
    RETENTION_V1_LOGS_VACUUM_MARKER,
    RETENTION_V1_MAIN_VACUUM_MARKER,
    DataRetentionService,
    resolve_retention_config,
)


def _stamp(days_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture
def service(wm_paths) -> AgentBridgeService:
    svc = AgentBridgeService.create(wm_paths, {"root"})
    return svc


@pytest.fixture
def retention(service) -> DataRetentionService:
    return service.data_retention


def _make_tool_log(store, log_id: str, days_ago: float) -> None:
    store.create_tool_call_log(
        log_id=log_id,
        actor="root",
        profile_key="dev",
        entrypoint="metamcp_execute",
        source_type="mcp_service",
        source_key="mysql",
        tool_name="query_sql",
        request={"sql": "select 1"},
        response={"rows": [1]},
        status=CallLogStatus.success.value,
    )
    with store.log_connect() as conn:
        conn.execute(
            "UPDATE tool_call_logs SET created_at = ? WHERE log_id = ?", (_stamp(days_ago), log_id)
        )


def _make_agent_run(store, run_key: str, days_ago: float, *, cwd: str | None = None) -> None:
    store.agent_runs.create(
        run_key=run_key,
        agent_name="design_workflow",
        profile_key="dev",
        ok=True,
        prompt="a long prompt" * 100,
        result={"answer": "ok"},
        events=[{"type": "tool_call"}],
        cwd=cwd,
    )
    with store.log_connect() as conn:
        conn.execute(
            "UPDATE agent_runs SET created_at = ? WHERE run_key = ?", (_stamp(days_ago), run_key)
        )


# -- 配置读取 / 保存 / 校验 --


def test_sync_config_retention_defaults_and_roundtrip(service) -> None:
    config = service.store.get_sync_config()
    assert config["retention_detail_days"] == 20
    assert config["retention_history_days"] == 60
    assert config["retention_cleanup_time"] == "22:00"
    resolved = resolve_retention_config(config)
    assert resolved == {
        "detail_days": 20,
        "history_days": 60,
        "cleanup_time": "22:00",
    }

    saved = service.save_sync_config(
        actor="root",
        code_sync_cron="0 * * * *",
        retention_detail_days=7,
        retention_history_days=30,
        retention_cleanup_time="03:30",
    )
    assert saved["retention_detail_days"] == 7
    assert saved["retention_history_days"] == 30
    assert saved["retention_cleanup_time"] == "03:30"
    reloaded = service.store.get_sync_config()
    assert reloaded["retention_detail_days"] == 7
    assert reloaded["retention_history_days"] == 30
    assert reloaded["retention_cleanup_time"] == "03:30"


def test_save_sync_config_validates_retention_fields(service) -> None:
    with pytest.raises(ValidationError):
        service.save_sync_config(
            actor="root",
            code_sync_cron="0 * * * *",
            retention_detail_days=90,
            retention_history_days=30,
        )
    with pytest.raises(ValidationError):
        service.save_sync_config(
            actor="root",
            code_sync_cron="0 * * * *",
            retention_cleanup_time="25:00",
        )
    with pytest.raises(ValidationError):
        service.save_sync_config(
            actor="root",
            code_sync_cron="0 * * * *",
            retention_detail_days=0,
        )


# -- 日志库：agent_runs / tool_call_logs 20 天瘦身 + 60 天删除 --


def test_runtime_logs_slim_and_delete_windows(service, retention) -> None:
    _make_tool_log(service.store, "tool_19d", 19)
    _make_tool_log(service.store, "tool_21d", 21)
    _make_tool_log(service.store, "tool_61d", 61)
    _make_agent_run(service.store, "run_19d", 19)
    _make_agent_run(service.store, "run_21d", 21)
    _make_agent_run(service.store, "run_61d", 61)

    summary = retention.run_daily_cleanup()

    assert summary["logs_db"]["deleted"] == {"agent_runs": 1, "tool_call_logs": 1}
    assert summary["logs_db"]["slimmed"] == {"agent_runs": 1, "tool_call_logs": 1}

    # 19 天：完整保留
    recent_tool = service.store.get_tool_call_log("tool_19d")
    assert recent_tool is not None and recent_tool["request_json"] != "{}"
    recent_run = service.store.agent_runs.get("run_19d")
    assert recent_run is not None and recent_run["prompt"]

    # 21 天：大字段清理、轻量审计字段保留
    slim_tool = service.store.get_tool_call_log("tool_21d")
    assert slim_tool is not None
    assert slim_tool["request_json"] == "{}"
    assert slim_tool["response_json"] == "{}"
    assert slim_tool["request_summary_json"]  # 摘要保留
    assert slim_tool["status"] == CallLogStatus.success.value
    slim_run = service.store.agent_runs.get("run_21d")
    assert slim_run is not None
    assert slim_run["prompt"] == ""
    with service.store.log_connect() as conn:
        slim_row = conn.execute(
            "SELECT events_json, result_json, output_schema_json, status, backend_key, duration_ms"
            " FROM agent_runs WHERE run_key = 'run_21d'"
        ).fetchone()
    assert slim_row["events_json"] == "[]"
    assert slim_row["result_json"] is None
    assert slim_row["output_schema_json"] is None
    assert slim_row["status"]  # 状态等元数据保留

    # 61 天：整行删除
    assert service.store.get_tool_call_log("tool_61d") is None
    assert service.store.agent_runs.get("run_61d") is None


def test_hourly_runtime_log_prune_no_longer_runs(service) -> None:
    """旧的写入路径 prune 机制移除：写日志不再触发清理，门面方法不存在。"""
    assert not hasattr(service.store, "maybe_prune_runtime_logs")
    assert not hasattr(service.store, "prune_runtime_logs")
    _make_tool_log(service.store, "tool_61d", 61)
    _make_agent_run(service.store, "run_61d", 61)
    assert service.store.get_tool_call_log("tool_61d") is not None
    assert service.store.agent_runs.get("run_61d") is not None


# -- Agent 运行目录：20 天清理，运行中目录保护 --


def test_agent_run_dirs_cleanup_protects_running(service, retention, wm_paths) -> None:
    base = wm_paths.run_dir / "agent-runs"
    base.mkdir(parents=True, exist_ok=True)
    running_dir = base / "agent_running_1"
    running_dir.mkdir()
    (running_dir / "messages.jsonl").write_text("{}", encoding="utf-8")
    finished_old_dir = base / "agent_old_1"
    finished_old_dir.mkdir()
    (finished_old_dir / "messages.jsonl").write_text("{}", encoding="utf-8")
    finished_recent_dir = base / "agent_recent_1"
    finished_recent_dir.mkdir()

    import os
    import time

    old_epoch = time.time() - 21 * 86400
    os.utime(finished_old_dir, (old_epoch, old_epoch))
    os.utime(running_dir, (old_epoch, old_epoch))  # 运行中即使目录陈旧也不删

    _make_agent_run(service.store, "run_active", 21, cwd=str(running_dir))
    with service.store.log_connect() as conn:
        conn.execute("UPDATE agent_runs SET status = 'running' WHERE run_key = 'run_active'")

    summary = retention.run_daily_cleanup()

    assert summary["dirs"]["agent_run_dirs"] == 1
    assert not finished_old_dir.exists()
    assert running_dir.exists()
    assert finished_recent_dir.exists()


# -- 主库 P0：workflow_run_logs / script_runs --


def test_workflow_run_logs_and_script_runs_windows(service, retention) -> None:
    with service.store.connect() as conn:
        conn.execute(
            "INSERT INTO workflow_run_logs (run_id, workflow_key, message, created_at) VALUES (?, ?, ?, ?)",
            ("run_x", "wf", "old log", _stamp(21)),
        )
        conn.execute(
            "INSERT INTO workflow_run_logs (run_id, workflow_key, message, created_at) VALUES (?, ?, ?, ?)",
            ("run_x", "wf", "recent log", _stamp(19)),
        )
        conn.execute(
            """
            INSERT INTO scripts (script_key, name, code, created_by, updated_by)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("s1", "S1", "print(1)", "root", "root"),
        )
        conn.execute(
            """
            INSERT INTO script_runs (run_id, script_key, run_type, params_json, result_json,
                                     stdout, stderr, status, created_by, created_at)
            VALUES (?, ?, 'manual', '{}', ?, ?, ?, 'completed', 'root', ?)
            """,
            ("sr_21d", "s1", json.dumps({"big": "x" * 100}), "out", "err", _stamp(21)),
        )
        conn.execute(
            """
            INSERT INTO script_runs (run_id, script_key, run_type, params_json, result_json,
                                     stdout, stderr, status, created_by, created_at)
            VALUES (?, ?, 'manual', '{}', ?, ?, ?, 'completed', 'root', ?)
            """,
            ("sr_61d", "s1", json.dumps({"big": "x" * 100}), "out", "err", _stamp(61)),
        )
        conn.execute(
            """
            INSERT INTO script_runs (run_id, script_key, run_type, params_json, result_json,
                                     stdout, stderr, status, created_by, created_at)
            VALUES (?, ?, 'manual', '{}', ?, ?, ?, 'completed', 'root', ?)
            """,
            ("sr_19d", "s1", json.dumps({"big": "x" * 100}), "out", "err", _stamp(19)),
        )

    summary = retention.run_daily_cleanup()

    assert summary["main_db"]["deleted"]["workflow_run_logs"] == 1
    assert summary["main_db"]["slimmed"]["script_runs"] == 1
    assert summary["main_db"]["deleted"]["script_runs"] == 1
    with service.store.connect() as conn:
        kept_recent_log = conn.execute(
            "SELECT COUNT(*) FROM workflow_run_logs WHERE message = 'recent log'"
        ).fetchone()[0]
        slim = conn.execute(
            "SELECT stdout, stderr, result_json, status FROM script_runs WHERE run_id = 'sr_21d'"
        ).fetchone()
        gone = conn.execute(
            "SELECT COUNT(*) FROM script_runs WHERE run_id = 'sr_61d'"
        ).fetchone()[0]
        full = conn.execute(
            "SELECT stdout FROM script_runs WHERE run_id = 'sr_19d'"
        ).fetchone()[0]
    assert kept_recent_log == 1
    assert slim["stdout"] == "" and slim["stderr"] == "" and slim["result_json"] == "{}"
    assert slim["status"] == "completed"
    assert gone == 0
    assert full == "out"


# -- P1：workflow_runs 级联 + 产物 + FTS + temp_dir --


def _seed_workflow_run(store, run_id: str, days_ago: float, *, temp_dir: str = "") -> None:
    store.upsert_project_profile(
        profile_key="dev",
        name="Dev Profile",
        created_by="root",
    )
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO workflow_definitions (workflow_key, name, profile_key, created_by)"
            " VALUES ('wf_ret', 'WF', 'dev', 'root')"
            " ON CONFLICT(workflow_key) DO NOTHING"
        )
        conn.execute(
            """
            INSERT INTO workflow_runs (run_id, workflow_key, profile_key, status, temp_dir,
                                       started_at, finished_at)
            VALUES (?, 'wf_ret', 'dev', 'completed', ?, ?, ?)
            """,
            (run_id, temp_dir, _stamp(days_ago), _stamp(days_ago)),
        )
        conn.execute(
            """
            INSERT INTO workflow_node_runs (run_id, node_id, node_type, status)
            VALUES (?, 'n1', 'agent', 'completed')
            """,
            (run_id,),
        )
        conn.execute(
            """
            INSERT INTO workflow_run_artifacts (run_id, node_id, artifact_id)
            VALUES (?, 'n1', 'art_1')
            """,
            (run_id,),
        )


def test_workflow_runs_cascade_artifacts_fts_and_temp_dirs(service, retention, wm_paths) -> None:
    temp_root = wm_paths.run_dir / "workflow-runs"
    old_dir = temp_root / "run_old"
    old_dir.mkdir(parents=True)
    (old_dir / "output.txt").write_text("x", encoding="utf-8")
    keep_dir = temp_root / "run_keep"
    keep_dir.mkdir(parents=True)

    _seed_workflow_run(service.store, "run_old", 61, temp_dir=str(old_dir))
    _seed_workflow_run(service.store, "run_keep", 10, temp_dir=str(keep_dir))

    # 历史产物（is_current=0，更新时间超窗）与当前产物各一条。
    service.store.upsert_workflow_artifact(
        workflow_key="wf_ret",
        profile_key="dev",
        run_id="run_old",
        task_key="t1",
        title="历史产物",
        path="reports/old.md",
        tags=[],
        format="markdown",
        summary="历史摘要",
        content="历史产物全文内容 unique_retention_marker",
        metadata={},
    )
    service.store.upsert_workflow_artifact(
        workflow_key="wf_ret",
        profile_key="dev",
        run_id="run_keep",
        task_key="t1",
        title="当前产物",
        path="reports/keep.md",
        tags=[],
        format="markdown",
        summary="当前摘要",
        content="当前产物全文内容 unique_retention_marker",
        metadata={},
    )
    # 手工把第一条降为历史并回退更新时间（upsert 会把最新 run 置 current）。
    with service.store.connect() as conn:
        conn.execute(
            "UPDATE workflow_artifacts SET is_current = 0, updated_at = ? WHERE run_id = 'run_old'",
            (_stamp(61),),
        )

    history = service.store.search_workflow_artifacts(
        profile_key="dev", query="unique_retention_marker", tags=[], path=None, workflow_key=None,
        limit=10, include_history=True,
    )
    assert sorted(item["run_id"] for item in history) == ["run_keep", "run_old"]

    summary = retention.run_daily_cleanup()

    assert summary["main_db"]["deleted"]["workflow_runs"] == 1
    assert summary["main_db"]["deleted"]["workflow_node_runs"] == 1
    assert summary["main_db"]["deleted"]["workflow_run_artifacts"] == 1
    assert summary["main_db"]["deleted"]["workflow_artifacts"] == 1
    assert not old_dir.exists()
    assert keep_dir.exists()

    with service.store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM workflow_runs WHERE run_id = 'run_old'").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM workflow_node_runs WHERE run_id = 'run_old'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM workflow_run_artifacts WHERE run_id = 'run_old'"
        ).fetchone()[0] == 0
        current_count = conn.execute(
            "SELECT COUNT(*) FROM workflow_artifacts WHERE is_current = 1"
        ).fetchone()[0]
        assert current_count == 1

    # FTS 与主表保持一致：删除后的历史产物不再命中，当前产物仍可检索。
    results = service.store.search_workflow_artifacts(
        profile_key="dev", query="unique_retention_marker", tags=[], path=None, workflow_key=None, limit=10
    )
    assert [item["run_id"] for item in results] == ["run_keep"]
    history = service.store.search_workflow_artifacts(
        profile_key="dev", query="unique_retention_marker", tags=[], path=None, workflow_key=None, limit=10, include_history=True
    )
    assert [item["run_id"] for item in history] == ["run_keep"]


# -- P1：模型评测 --


def test_model_evaluation_cleanup_with_work_dir(service, retention, wm_paths) -> None:
    work_root = wm_paths.run_dir / "model-evaluations"
    old_dir = work_root / "eval_old"
    old_dir.mkdir(parents=True)
    keep_dir = work_root / "eval_keep"
    keep_dir.mkdir(parents=True)
    with service.store.connect() as conn:
        conn.execute(
            """
            INSERT INTO model_evaluation_runs (run_id, model_name, base_url, datasets_json,
                                               status, work_dir, created_by, created_at)
            VALUES ('eval_old', 'm1', 'http://x', '[]', 'completed', ?, 'root', ?)
            """,
            (str(old_dir), datetime.now(UTC).isoformat()),
        )
        conn.execute(
            "UPDATE model_evaluation_runs SET created_at = ? WHERE run_id = 'eval_old'",
            (_stamp(61),),
        )
        conn.execute(
            """
            INSERT INTO model_evaluation_runs (run_id, model_name, base_url, datasets_json,
                                               status, work_dir, created_by, created_at)
            VALUES ('eval_keep', 'm1', 'http://x', '[]', 'completed', ?, 'root', ?)
            """,
            (str(keep_dir), _stamp(3)),
        )
        conn.execute(
            """
            INSERT INTO model_evaluation_executions (execution_id, run_id, runner_key,
                                                     datasets_json, image, status, work_dir, created_at)
            VALUES ('exec_old', 'eval_old', 'docker', '[]', 'img', 'completed', ?, ?)
            """,
            (str(old_dir / "executions" / "docker"), _stamp(61)),
        )

    summary = retention.run_daily_cleanup()

    assert summary["main_db"]["deleted"]["model_evaluation_runs"] == 1
    assert summary["main_db"]["deleted"]["model_evaluation_executions"] == 1
    assert not old_dir.exists()
    assert keep_dir.exists()
    with service.store.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM model_evaluation_executions WHERE run_id = 'eval_old'"
        ).fetchone()[0] == 0


# -- P2：sync_jobs / codegraph_sync_runs / imports --


def test_p2_low_risk_history_cleanup(service, retention) -> None:
    service.store.upsert_project_profile(profile_key="dev", name="Dev Profile", created_by="root")
    with service.store.connect() as conn:
        conn.execute(
            "INSERT INTO workflow_definitions (workflow_key, name, profile_key, created_by)"
            " VALUES ('wf', 'WF', 'dev', 'root')"
            " ON CONFLICT(workflow_key) DO NOTHING"
        )
        conn.execute(
            "INSERT INTO knowledge_bases (slug, name, created_by) VALUES ('kb1', 'KB1', 'root')"
        )
        conn.execute(
            "INSERT INTO documents (slug, title, owner_user) VALUES ('doc1', 'D1', 'root')"
        )
        conn.execute(
            "INSERT INTO sync_jobs (doc_id, kb_id, operation, status, created_at, updated_at)"
            " VALUES ((SELECT id FROM documents WHERE slug = 'doc1'),"
            "         (SELECT id FROM knowledge_bases WHERE slug = 'kb1'), 'upsert', 'succeeded', ?, ?)",
            (_stamp(61), _stamp(61)),
        )
        conn.execute(
            "INSERT INTO sync_jobs (doc_id, kb_id, operation, status, created_at, updated_at)"
            " VALUES ((SELECT id FROM documents WHERE slug = 'doc1'),"
            "         (SELECT id FROM knowledge_bases WHERE slug = 'kb1'), 'upsert', 'succeeded', ?, ?)",
            (_stamp(10), _stamp(10)),
        )
        conn.execute(
            "INSERT INTO code_repositories (repo_key, name, git_url) VALUES ('repo_x', 'X', 'http://x')"
        )
        conn.execute(
            "INSERT INTO codegraph_sync_runs (repo_key, status, stage, started_at, finished_at)"
            " VALUES ('repo_x', 'succeeded', 'done', ?, ?)",
            (_stamp(61), _stamp(61)),
        )
        conn.execute(
            "INSERT INTO codegraph_sync_runs (repo_key, status, stage, started_at, finished_at)"
            " VALUES ('repo_x', 'succeeded', 'done', ?, ?)",
            (_stamp(3), _stamp(3)),
        )
        conn.execute(
            "INSERT INTO workflow_task_imports (import_id, workflow_key, actor, filename,"
            " sheet_name, tasks_json, preview_json, expires_at)"
            " VALUES ('imp_expired', 'wf', 'root', 'f.xlsx', 'S', '[]', '{}', ?)",
            (_stamp(1),),
        )
        conn.execute(
            "INSERT INTO workflow_task_imports (import_id, workflow_key, actor, filename,"
            " sheet_name, tasks_json, preview_json, expires_at)"
            " VALUES ('imp_alive', 'wf', 'root', 'f.xlsx', 'S', '[]', '{}', ?)",
            (_stamp(-1),),
        )
        conn.execute(
            "INSERT INTO workflow_definition_imports (import_id, actor, filename,"
            " source_workflow_key, target_workflow_key, operation, workflow_json, expires_at)"
            " VALUES ('def_imp_expired', 'root', 'f.json', 'a', 'b', 'copy', '{}', ?)",
            (_stamp(1),),
        )

    summary = retention.run_daily_cleanup()

    assert summary["main_db"]["deleted"]["sync_jobs"] == 1
    assert summary["main_db"]["deleted"]["codegraph_sync_runs"] == 1
    assert summary["main_db"]["deleted"]["workflow_task_imports"] == 1
    assert summary["main_db"]["deleted"]["workflow_definition_imports"] == 1
    with service.store.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM sync_jobs WHERE status = 'succeeded'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM codegraph_sync_runs"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM workflow_task_imports"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM workflow_definition_imports"
        ).fetchone()[0] == 0


# -- 明确不清理：ledgers --


def test_ledger_data_never_touched(service, retention, wm_paths) -> None:
    service.business_ledgers.init_schema()
    conn = sqlite3.connect(wm_paths.ledger_db_path)
    try:
        conn.execute(
            """
            INSERT INTO business_ledgers (ledger_key, name, definition_json, created_by, owner_group_key, created_at, updated_at)
            VALUES ('ledger_old', 'L', '{}', 'root', 'g', ?, ?)
            """,
            (_stamp(365), _stamp(365)),
        )
        conn.commit()
    finally:
        conn.close()

    retention.run_first_upgrade()

    conn = sqlite3.connect(wm_paths.ledger_db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM business_ledgers").fetchone()[0]
    finally:
        conn.close()
    assert count == 1


# -- 首次升级迁移：marker、幂等、部分完成恢复 --


def test_first_upgrade_markers_and_idempotency(service, retention) -> None:
    _make_tool_log(service.store, "tool_61d", 61)
    first = retention.run_first_upgrade()

    assert not first["stages"]["cleanup"].get("skipped")
    assert not first["stages"]["main_vacuum"].get("skipped")
    assert not first["stages"]["logs_vacuum"].get("skipped")
    assert first["stages"]["ledger_vacuum"]["outcome"] == "skipped"
    assert service.store.get_tool_call_log("tool_61d") is None

    def _marker_values() -> dict[str, str]:
        with service.store.connect() as conn:
            rows = conn.execute("SELECT key, value FROM data_retention_meta").fetchall()
        return {row["key"]: row["value"] for row in rows}

    markers = _marker_values()
    assert markers[RETENTION_V1_CLEANUP_MARKER] == "done"
    assert markers[RETENTION_V1_MAIN_VACUUM_MARKER] == "done"
    assert markers[RETENTION_V1_LOGS_VACUUM_MARKER] == "done"
    assert markers[RETENTION_V1_LEDGER_VACUUM_MARKER] == "skipped"

    # 重复启动：全部阶段跳过。
    second = retention.run_first_upgrade()
    for stage in second["stages"].values():
        assert stage.get("skipped") == "marker_done"

    # 部分完成恢复：删掉主库 VACUUM marker 后，仅该阶段重跑，清理不重复执行。
    vacuum_calls: list[str] = []
    original_vacuum = DataRetentionService._vacuum_database

    def _spy_vacuum(self, db_path: Path, *, label: str) -> float:
        vacuum_calls.append(label)
        return 0.0

    DataRetentionService._vacuum_database = _spy_vacuum  # type: ignore[assignment]
    try:
        with service.store.connect() as conn:
            conn.execute(
                f"DELETE FROM data_retention_meta WHERE key = '{RETENTION_V1_MAIN_VACUUM_MARKER}'"
            )
        third = retention.run_first_upgrade()
    finally:
        DataRetentionService._vacuum_database = original_vacuum  # type: ignore[assignment]
    assert third["stages"]["cleanup"].get("skipped") == "marker_done"
    assert third["stages"]["logs_vacuum"].get("skipped") == "marker_done"
    assert not third["stages"]["main_vacuum"].get("skipped")
    assert [label for label in vacuum_calls if "主库" in label]


def test_daily_cleanup_never_vacuums(service, retention) -> None:
    retention.run_first_upgrade()
    original_vacuum = DataRetentionService._vacuum_database

    def _fail_vacuum(self, db_path: Path, *, label: str) -> float:
        raise AssertionError("日常清理不允许执行 VACUUM")

    DataRetentionService._vacuum_database = _fail_vacuum  # type: ignore[assignment]
    try:
        retention.run_daily_cleanup()
    finally:
        DataRetentionService._vacuum_database = original_vacuum  # type: ignore[assignment]


# -- 旧 log_retention_days 迁移兼容 --


def test_legacy_log_retention_days_migrates_once(wm_paths) -> None:
    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    with svc.store.connect() as conn:
        conn.execute(
            """
            INSERT INTO knowledge_sync_config (id, log_retention_days)
            VALUES (1, 90)
            """
        )

    svc.data_retention.run_first_upgrade()

    config = svc.store.get_sync_config()
    assert config["retention_history_days"] == 90
    assert config["retention_detail_days"] == 20

    # 迁移只执行一次：之后修改 log_retention_days 不再影响生命周期配置。
    with svc.store.connect() as conn:
        conn.execute("UPDATE knowledge_sync_config SET log_retention_days = 30 WHERE id = 1")
    svc.data_retention.run_first_upgrade()
    config = svc.store.get_sync_config()
    assert config["retention_history_days"] == 90


def test_legacy_default_log_retention_keeps_new_defaults(wm_paths) -> None:
    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    with svc.store.connect() as conn:
        conn.execute(
            "INSERT INTO knowledge_sync_config (id, log_retention_days) VALUES (1, 180)"
        )
    svc.data_retention.run_first_upgrade()
    config = svc.store.get_sync_config()
    assert config["retention_detail_days"] == 20
    assert config["retention_history_days"] == 60


# -- 分批删除 --


def test_batched_delete_removes_all_rows_across_batches(service, retention) -> None:
    total = 1200  # 默认批 500，需 3 批
    with service.store.connect() as conn:
        conn.executemany(
            "INSERT INTO workflow_run_logs (run_id, workflow_key, message, created_at)"
            " VALUES ('bulk', 'wf', 'm', ?)",
            ((_stamp(21),) for _ in range(total)),
        )
    summary = retention.run_daily_cleanup()
    assert summary["main_db"]["deleted"]["workflow_run_logs"] == total
    with service.store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM workflow_run_logs").fetchone()[0] == 0
