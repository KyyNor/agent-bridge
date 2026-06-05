from __future__ import annotations

import asyncio
import json

import pytest

from test_capability_service import FakeMcpClient
from agent_bridge.capabilities import CallLogStatus, FailureOwner, FailureStage, SourceType, ToolType
from agent_bridge.capability_service import CapabilityService
from agent_bridge.config import AgentBridgePaths
from agent_bridge.domain import ValidationError
from agent_bridge.storage import SQLiteStore


def test_tool_call_log_records_failure_classification_and_resource(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()

    request = {"sql": "select 1"}
    response = {"error": "transport unavailable"}
    log = store.create_tool_call_log(
        log_id="call_failure_classification",
        actor="root",
        profile_key="safe-readonly",
        entrypoint="metamcp_execute",
        source_type=SourceType.mcp_service.value,
        source_key="mysql",
        tool_name="query_sql",
        resource_type="service",
        resource_key="mysql",
        request=request,
        response=response,
        status=CallLogStatus.error.value,
        error_message="transport unavailable",
        failure_stage=FailureStage.mcp_transport.value,
        failure_owner=FailureOwner.upstream_mcp.value,
        error_type="mcp_transport_error",
        duration_ms=25,
    )

    assert log["failure_stage"] == "mcp_transport"
    assert log["failure_owner"] == "upstream_mcp"
    assert log["error_type"] == "mcp_transport_error"
    assert log["resource_type"] == "service"
    assert log["resource_key"] == "mysql"
    assert json.loads(log["request_summary_json"]) == {
        "keys": ["sql"],
        "bytes": len(json.dumps(request, ensure_ascii=False, default=str).encode("utf-8")),
    }
    assert json.loads(log["response_summary_json"]) == {
        "keys": ["error"],
        "bytes": len(json.dumps(response, ensure_ascii=False, default=str).encode("utf-8")),
    }


def test_tool_call_log_filters_by_failure_and_time_range(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.create_tool_call_log(
        log_id="call_policy_block",
        actor="root",
        profile_key="safe-readonly",
        entrypoint="metamcp_execute",
        source_type=SourceType.mcp_service.value,
        source_key="hive",
        tool_name="query_sql",
        request={},
        response={"error": "blocked"},
        status=CallLogStatus.blocked.value,
        failure_stage=FailureStage.profile_policy.value,
        failure_owner=FailureOwner.policy.value,
        error_type="profile_policy_blocked",
    )
    store.create_tool_call_log(
        log_id="call_success",
        actor="root",
        profile_key="safe-readonly",
        entrypoint="metamcp_execute",
        source_type=SourceType.mcp_service.value,
        source_key="mysql",
        tool_name="query_sql",
        request={},
        response={"rows": []},
        status=CallLogStatus.success.value,
    )

    filtered = store.list_tool_call_logs(
        profile_key="safe-readonly",
        failure_stage=FailureStage.profile_policy.value,
        failure_owner=FailureOwner.policy.value,
        error_type="profile_policy_blocked",
    )

    assert [item["log_id"] for item in filtered] == ["call_policy_block"]


def test_execute_classifies_profile_block(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    service = CapabilityService(store=store, mcp_client=FakeMcpClient(), admins={"root"})
    service.register_service("root", "hive", "Hive", "https://hive.test/mcp", {}, "Hive service", ["db"])
    service.governance.upsert_profile("root", "safe-readonly", "安全只读", "", "active")
    service.governance.replace_profile_rules(
        "root",
        "safe-readonly",
        [{"source_type": "mcp_service", "source_key": "hive", "effect": "deny"}],
    )

    with pytest.raises(ValidationError):
        asyncio.run(service.execute("root", "hive", "query_sql", {}, profile_key="safe-readonly"))

    log = service.governance.list_logs(actor="root", status="blocked")[0]
    assert log["failure_stage"] == FailureStage.profile_policy.value
    assert log["failure_owner"] == FailureOwner.policy.value
    assert log["error_type"] == "profile_policy_blocked"


def test_execute_classifies_mcp_transport_error(wm_paths: AgentBridgePaths) -> None:
    class FailingCallMcpClient(FakeMcpClient):
        async def call_tool(self, endpoint_url, headers, tool_name, arguments):
            raise RuntimeError("transport unavailable")

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    service = CapabilityService(store=store, mcp_client=FailingCallMcpClient(), admins={"root"})
    service.register_service("root", "docs-api", "Docs API", "https://example.test/mcp", {}, "", [])
    asyncio.run(service.sync_tools("root", "docs-api"))
    service.set_tool_type("root", "docs-api", "search_docs", ToolType.search.value)

    with pytest.raises(ValidationError):
        asyncio.run(service.execute("root", "docs-api", "search_docs", {"query": "hello"}))

    log = service.governance.list_logs(actor="root", status="error")[0]
    assert log["failure_stage"] == FailureStage.mcp_transport.value
    assert log["failure_owner"] == FailureOwner.upstream_mcp.value
    assert log["error_type"] == "mcp_transport_error"
