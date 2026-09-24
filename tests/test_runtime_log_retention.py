"""运行日志保留的旧机制退役回归。

runtime log 不再有独立的清理通道：写入路径不触发 prune，保存系统配置后的
立即清理由统一的数据生命周期任务承担（详见 ``test_data_retention.py``）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agent_bridge.app.service import AgentBridgeService
from agent_bridge.capability_hub.models import CallLogStatus


def _stamp(days_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


def test_writing_runtime_logs_does_not_prune(wm_paths) -> None:
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
    old_created_at = _stamp(400)
    with service.store.log_connect() as conn:
        conn.execute(
            "UPDATE tool_call_logs SET created_at = ? WHERE log_id = ?",
            (old_created_at, old_log["log_id"]),
        )
        conn.execute(
            "UPDATE agent_runs SET created_at = ? WHERE run_key = ?",
            (old_created_at, old_run["run_key"]),
        )

    # 再写一条新日志：旧行仍在——写入路径不再附带清理。
    service.store.create_tool_call_log(
        log_id="fresh_tool_log",
        actor="root",
        profile_key="dev",
        entrypoint="metamcp_execute",
        status=CallLogStatus.success.value,
    )
    service.store.agent_runs.create(
        run_key="fresh_agent_run",
        agent_name="design_script",
        profile_key="dev",
        ok=True,
        prompt="y",
        events=[],
    )
    assert service.store.get_tool_call_log(old_log["log_id"]) is not None
    assert service.store.agent_runs.get(old_run["run_key"]) is not None


def test_save_sync_config_cleans_up_with_new_retention_config(wm_paths) -> None:
    service = AgentBridgeService.create(wm_paths, {"root"})
    service.store.init_schema()

    old_log = service.store.create_tool_call_log(
        log_id="old_tool_log",
        actor="root",
        profile_key="dev",
        entrypoint="metamcp_execute",
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
    old_created_at = _stamp(31)
    with service.store.log_connect() as conn:
        conn.execute(
            "UPDATE tool_call_logs SET created_at = ? WHERE log_id = ?",
            (old_created_at, old_log["log_id"]),
        )
        conn.execute(
            "UPDATE agent_runs SET created_at = ? WHERE run_key = ?",
            (old_created_at, old_run["run_key"]),
        )

    # 保存 history=30：31 天的记录按新配置立即删除。
    result = service.save_sync_config(
        actor="root",
        code_sync_cron="0 * * * *",
        retention_detail_days=7,
        retention_history_days=30,
    )

    assert result["retention_history_days"] == 30
    assert service.store.get_tool_call_log(old_log["log_id"]) is None
    assert service.store.agent_runs.get(old_run["run_key"]) is None
