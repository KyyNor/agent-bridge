from __future__ import annotations

from agent_bridge.capabilities.models import CallLogStatus, SourceType
from agent_bridge.core.config import AgentBridgePaths
from agent_bridge.storage.sqlite import SQLiteStore


def test_call_log_stats_group_by_profile_service_and_tool(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    rows = [
        ("call_1", "safe", "mysql", "query_sql", "success", None, None, None, 10),
        ("call_2", "safe", "mysql", "query_sql", "error", "mcp_transport", "upstream_mcp", "mcp_transport_error", 100),
        ("call_3", "safe", "hive", "query_sql", "blocked", "profile_policy", "policy", "profile_policy_blocked", 1),
    ]
    for log_id, profile, service, tool, status, stage, owner, error_type, duration in rows:
        store.create_tool_call_log(
            log_id=log_id,
            actor="root",
            profile_key=profile,
            entrypoint="metamcp_execute",
            source_type=SourceType.mcp_service.value,
            source_key=service,
            tool_name=tool,
            request={},
            response={},
            status=status,
            failure_stage=stage,
            failure_owner=owner,
            error_type=error_type,
            duration_ms=duration,
        )

    stats = store.aggregate_tool_call_stats(
        dimensions=["profile_key", "source_key", "tool_name"],
        created_from=None,
        created_to=None,
        bucket=None,
    )

    mysql = next(row for row in stats if row["source_key"] == "mysql")
    hive = next(row for row in stats if row["source_key"] == "hive")
    assert mysql["calls"] == 2
    assert mysql["success"] == 1
    assert mysql["error"] == 1
    assert mysql["blocked"] == 0
    assert mysql["avg_duration_ms"] == 55
    assert mysql["max_duration_ms"] == 100
    assert hive["blocked"] == 1
