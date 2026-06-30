from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agent_bridge.app.service import AgentBridgeService
from agent_bridge.capability_hub.models import CallLogStatus
from agent_bridge.storage.sqlite import SQLiteStore


def test_prune_runtime_logs_removes_old_tool_and_agent_rows(wm_paths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.set_runtime_log_retention_days(180)

    recent_log = store.create_tool_call_log(
        log_id="recent_tool_log",
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
    old_log = store.create_tool_call_log(
        log_id="old_tool_log",
        actor="root",
        profile_key="dev",
        entrypoint="memory_hook_claude_code",
        source_type="hook",
        source_key="claude_code",
        tool_name="observation",
        request={"payload": {"tool_name": "Read"}},
        response={"status": "ok"},
        status=CallLogStatus.success.value,
    )
    recent_run = store.agent_runs.create(
        run_key="recent_agent_run",
        agent_name="design_workflow",
        profile_key="dev",
        ok=True,
        prompt="hi",
        result="ok",
        events=[],
    )
    old_run = store.agent_runs.create(
        run_key="old_agent_run",
        agent_name="design_script",
        profile_key="dev",
        ok=False,
        prompt="x",
        error="boom",
        events=[],
    )

    old_created_at = (datetime.now(UTC) - timedelta(days=181)).strftime("%Y-%m-%d %H:%M:%S")
    with store.connect() as conn:
        conn.execute("UPDATE tool_call_logs SET created_at = ? WHERE log_id = ?", (old_created_at, old_log["log_id"]))
        conn.execute("UPDATE agent_runs SET created_at = ? WHERE run_key = ?", (old_created_at, old_run["run_key"]))

    deleted = store.prune_runtime_logs(force=True)

    assert deleted == {"tool_call_logs": 1, "agent_runs": 1}
    assert store.get_tool_call_log(old_log["log_id"]) is None
    assert store.agent_runs.get(old_run["run_key"]) is None
    assert store.get_tool_call_log(recent_log["log_id"]) is not None
    assert store.agent_runs.get(recent_run["run_key"]) is not None


def test_save_sync_config_updates_retention_and_prunes_immediately(wm_paths) -> None:
    service = AgentBridgeService.create(wm_paths, {"root"})
    service.store.init_schema()

    old_log = service.store.create_tool_call_log(
        log_id="old_tool_log",
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
    old_run = service.store.agent_runs.create(
        run_key="old_agent_run",
        agent_name="design_script",
        profile_key="dev",
        ok=False,
        prompt="x",
        error="boom",
        events=[],
    )
    old_created_at = (datetime.now(UTC) - timedelta(days=31)).strftime("%Y-%m-%d %H:%M:%S")
    with service.store.connect() as conn:
        conn.execute("UPDATE tool_call_logs SET created_at = ? WHERE log_id = ?", (old_created_at, old_log["log_id"]))
        conn.execute("UPDATE agent_runs SET created_at = ? WHERE run_key = ?", (old_created_at, old_run["run_key"]))

    result = service.save_sync_config(actor="root", code_sync_cron="0 * * * *", log_retention_days=30)

    assert result["log_retention_days"] == 30
    assert service.store.get_sync_config()["log_retention_days"] == 30
    assert service.store.get_tool_call_log(old_log["log_id"]) is None
    assert service.store.agent_runs.get(old_run["run_key"]) is None
