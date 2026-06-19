from __future__ import annotations

from agent_bridge.capability_hub.governance import CapabilityGovernanceService
from agent_bridge.capability_hub.models import CallLogStatus, SourceType, ToolType
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


def test_call_stats_group_by_service_and_tool_type(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.create_mcp_service(
        service_key="mysql",
        name="MySQL",
        endpoint_url="https://mysql.test/mcp",
        headers={},
        description="",
        tags=[],
        created_by="root",
    )
    store.upsert_mcp_tool(
        service_key="mysql",
        tool_name="query_sql",
        display_name="Query SQL",
        description="Run readonly SQL",
        input_schema={"type": "object"},
        tool_type=ToolType.search.value,
        tags=[],
        examples=[],
    )
    store.create_tool_call_log(
        log_id="call_1",
        actor="root",
        profile_key="safe",
        entrypoint="metamcp_execute",
        source_type=SourceType.mcp_service.value,
        source_key="mysql",
        tool_name="query_sql",
        request={},
        response={},
        status=CallLogStatus.success.value,
    )

    stats = store.aggregate_tool_call_stats(
        dimensions=["source_key", "tool_type"],
        created_from=None,
        created_to=None,
        bucket=None,
    )

    assert stats == [
        {
            "source_key": "mysql",
            "tool_type": "search",
            "calls": 1,
            "success": 1,
            "error": 0,
            "blocked": 0,
            "avg_duration_ms": 0.0,
            "max_duration_ms": None,
        }
    ]


def test_call_stats_tool_type_only_joins_mcp_service_logs(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.create_mcp_service(
        service_key="mysql",
        name="MySQL",
        endpoint_url="https://mysql.test/mcp",
        headers={},
        description="",
        tags=[],
        created_by="root",
    )
    store.upsert_mcp_tool(
        service_key="mysql",
        tool_name="query_sql",
        display_name="Query SQL",
        description="Run readonly SQL",
        input_schema={"type": "object"},
        tool_type=ToolType.search.value,
        tags=[],
        examples=[],
    )
    for log_id, source_type in [
        ("call_mcp", SourceType.mcp_service.value),
        ("call_builtin", SourceType.builtin.value),
    ]:
        store.create_tool_call_log(
            log_id=log_id,
            actor="root",
            profile_key="safe",
            entrypoint="metamcp_execute",
            source_type=source_type,
            source_key="mysql",
            tool_name="query_sql",
            request={},
            response={},
            status=CallLogStatus.success.value,
        )

    stats = store.aggregate_tool_call_stats(
        dimensions=["source_type", "tool_type"],
        created_from=None,
        created_to=None,
        bucket=None,
    )

    rows = {(row["source_type"], row["tool_type"]): row for row in stats}
    assert rows[(SourceType.mcp_service.value, ToolType.search.value)]["calls"] == 1
    assert rows[(SourceType.builtin.value, None)]["calls"] == 1
    assert (SourceType.builtin.value, ToolType.search.value) not in rows


def test_governance_stats_accepts_tool_type_dimension(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    service = CapabilityGovernanceService(store=store, admins={"root"})

    result = service.stats(
        actor="root",
        dimensions=["source_key", "tool_type"],
        created_from=None,
        created_to=None,
        bucket=None,
    )

    assert result["dimensions"] == ["source_key", "tool_type"]
    assert result["items"] == []
