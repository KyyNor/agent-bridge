from __future__ import annotations

import asyncio
import json

import pytest

from agent_bridge.capability_hub.models import CallLogStatus, McpServiceStatus, ToolType
from agent_bridge.capability_hub.service import CapabilityService
from agent_bridge.core.config import AgentBridgePaths
from agent_bridge.core.domain import AccessDenied, NotFound, ValidationError
from agent_bridge.storage.sqlite import SQLiteStore


class FakeMcpClient:
    def __init__(self) -> None:
        self.list_tools_calls: list[dict[str, object]] = []
        self.call_tool_calls: list[dict[str, object]] = []
        self.tools: list[dict[str, object]] | None = None
        self.call_result: dict[str, object] = {
            "is_error": False,
            "structured": {"rows": [{"id": 1}]},
            "content": [],
        }

    async def list_tools(
        self,
        endpoint_url: str,
        headers: dict[str, str],
        timeout: float = 30.0,
    ) -> list[dict[str, object]]:
        self.list_tools_calls.append({"endpoint_url": endpoint_url, "headers": headers, "timeout": timeout})
        if self.tools is not None:
            return self.tools
        return [
            {
                "name": "search_docs",
                "description": "Search documents",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
                "annotations": {"readOnlyHint": True},
                "output_schema": {"type": "object"},
            },
            {
                "name": "delete_doc",
                "description": "Delete a document",
                "input_schema": {
                    "type": "object",
                    "properties": {"doc_id": {"type": "string"}},
                    "required": ["doc_id"],
                },
                "annotations": {"destructiveHint": True},
                "output_schema": {"type": "object"},
            },
        ]

    async def call_tool(
        self,
        endpoint_url: str,
        headers: dict[str, str],
        tool_name: str,
        arguments: dict[str, object],
        timeout: float = 30.0,
    ) -> dict[str, object]:
        self.call_tool_calls.append(
            {
                "endpoint_url": endpoint_url,
                "headers": headers,
                "tool_name": tool_name,
                "params": arguments,
                "timeout": timeout,
            }
        )
        return self.call_result


def _service(wm_paths: AgentBridgePaths) -> tuple[CapabilityService, FakeMcpClient]:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    mcp_client = FakeMcpClient()
    return CapabilityService(store=store, mcp_client=mcp_client, admins={"root"}), mcp_client


def test_register_service_creates_updates_and_lists_parsed_payload(wm_paths: AgentBridgePaths) -> None:
    service, _client = _service(wm_paths)

    created = service.register_service(
        actor="root",
        service_key="docs-api",
        name="Docs API",
        endpoint_url="https://example.test/mcp",
        headers={"Authorization": "Bearer first"},
        description="Document capabilities",
        tags=["docs"],
    )
    updated = service.register_service(
        actor="root",
        service_key="docs-api",
        name="Docs API v2",
        endpoint_url="https://example.test/v2/mcp",
        headers={"Authorization": "Bearer second", "X-Empty": ""},
        description="Updated document capabilities",
        tags=["docs", "search"],
    )

    assert created["service_key"] == "docs-api"
    assert updated["name"] == "Docs API v2"
    assert service.list_services(actor="alice")[0]["headers"] == {"Authorization": "***", "X-Empty": ""}
    assert service.list_services(actor="alice")[0]["tags"] == ["docs", "search"]
    assert "headers_json" not in service.list_services(actor="alice")[0]
    assert "tags_json" not in service.list_services(actor="alice")[0]


def test_register_service_requires_admin_and_valid_service_key(wm_paths: AgentBridgePaths) -> None:
    service, _client = _service(wm_paths)

    with pytest.raises(AccessDenied):
        service.register_service(
            actor="alice",
            service_key="docs-api",
            name="Docs API",
            endpoint_url="https://example.test/mcp",
            headers={},
            description="Document capabilities",
            tags=[],
        )

    with pytest.raises(ValidationError, match="service_key"):
        service.register_service(
            actor="root",
            service_key="docs/api",
            name="Docs API",
            endpoint_url="https://example.test/mcp",
            headers={},
            description="Document capabilities",
            tags=[],
        )


def test_set_service_status_requires_admin_validates_status_and_missing_service(wm_paths: AgentBridgePaths) -> None:
    service, _client = _service(wm_paths)
    service.register_service(
        "root", "docs-api", "Docs API", "https://example.test/mcp", {}, "", [],
        visibility="shared",
    )

    with pytest.raises(AccessDenied):
        service.set_service_status("alice", "docs-api", McpServiceStatus.disabled.value)
    with pytest.raises(ValidationError, match="invalid service status"):
        service.set_service_status("root", "docs-api", "paused")
    with pytest.raises(NotFound, match="service not found"):
        service.set_service_status("root", "missing-api", McpServiceStatus.disabled.value)


def test_sync_tools_stores_tools_and_passes_headers(wm_paths: AgentBridgePaths) -> None:
    service, client = _service(wm_paths)
    service.register_service(
        actor="root",
        service_key="docs-api",
        name="Docs API",
        endpoint_url="https://example.test/mcp",
        headers={"Authorization": "Bearer token", "X-Tenant": "docs"},
        description="Document capabilities",
        tags=["docs"],
    )

    result = asyncio.run(service.sync_tools("root", "docs-api"))

    assert result == {"service_key": "docs-api", "tool_count": 2}
    assert client.list_tools_calls == [
        {
            "endpoint_url": "https://example.test/mcp",
            "headers": {"Authorization": "Bearer token", "X-Tenant": "docs"},
            "timeout": 150.0,
        }
    ]
    tools = service.list_tools("alice", "docs-api")
    assert [tool["tool"] for tool in tools] == ["delete_doc", "search_docs"]
    assert {tool["tool"]: tool["tool_type"] for tool in tools} == {
        "delete_doc": ToolType.unconfigured.value,
        "search_docs": ToolType.unconfigured.value,
    }


def test_execute_passes_configured_mcp_timeout(wm_paths: AgentBridgePaths) -> None:
    service, client = _service(wm_paths)
    service.store.save_sync_config(code_sync_cron="0 * * * *", mcp_timeout_seconds=150)
    service.register_service(
        actor="root",
        service_key="docs-api",
        name="Docs API",
        endpoint_url="https://example.test/mcp",
        headers={"Authorization": "Bearer token"},
        description="Document capabilities",
        tags=["docs"],
    )
    asyncio.run(service.sync_tools("root", "docs-api"))
    service.set_tool_type("root", "docs-api", "search_docs", ToolType.search.value)

    result = asyncio.run(service.execute("root", "docs-api", "search_docs", {"query": "routing"}))

    assert result["result"]["structured"] == {"rows": [{"id": 1}]}
    assert client.call_tool_calls[-1]["timeout"] == 150.0


def test_sync_tools_marks_failure(wm_paths: AgentBridgePaths) -> None:
    class FailingMcpClient(FakeMcpClient):
        async def list_tools(
            self,
            endpoint_url: str,
            headers: dict[str, str],
            timeout: float = 150.0,
        ) -> list[dict[str, object]]:
            raise RuntimeError("mcp unavailable")

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    service = CapabilityService(store=store, mcp_client=FailingMcpClient(), admins={"root"})
    service.register_service("root", "docs-api", "Docs API", "https://example.test/mcp", {}, "", [])

    with pytest.raises(ValidationError, match="MCP tool sync failed: mcp unavailable"):
        asyncio.run(service.sync_tools("root", "docs-api"))

    listed = service.list_services("root")
    assert listed[0]["status"] == McpServiceStatus.error.value
    assert listed[0]["last_error"] == "mcp unavailable"


def test_search_root_and_service_path_filters_by_query(wm_paths: AgentBridgePaths) -> None:
    service, _client = _service(wm_paths)
    service.register_service("root", "docs-api", "Docs API", "https://example.test/mcp", {}, "Document capabilities", ["docs"])
    service.register_service("root", "admin-api", "Admin API", "https://example.test/admin", {}, "Admin capabilities", ["admin"])
    service.set_service_status("root", "admin-api", McpServiceStatus.disabled.value)
    asyncio.run(service.sync_tools("root", "docs-api"))

    root = service.search(actor="alice", path="/", query=None)
    tools = service.search(actor="alice", path="docs-api", query="Search")

    assert root["path"] == "/"
    assert root["log_id"].startswith("call_")
    assert root["items"] == [
        {
            "kind": "service",
            "service": "docs-api",
            "name": "Docs API",
            "description": "Document capabilities",
            "tags": ["docs"],
            "tool_count": 2,
            "status": McpServiceStatus.enabled.value,
        }
    ]
    assert tools["path"] == "docs-api"
    assert tools["log_id"].startswith("call_")
    assert len(tools["items"]) == 1
    assert tools["items"][0]["kind"] == "tool"
    assert tools["items"][0]["service"] == "docs-api"
    assert tools["items"][0]["tool"] == "search_docs"
    assert tools["items"][0]["execute_example"] == {"query": "<string>"}
    assert tools["items"][0]["executable"] is False


@pytest.mark.parametrize("status", [McpServiceStatus.disabled, McpServiceStatus.error])
def test_disabled_or_error_service_blocks_direct_tool_visibility_and_execute(
    wm_paths: AgentBridgePaths,
    status: McpServiceStatus,
) -> None:
    service, client = _service(wm_paths)
    service.register_service("root", "docs-api", "Docs API", "https://example.test/mcp", {}, "Document capabilities", ["docs"])
    asyncio.run(service.sync_tools("root", "docs-api"))
    service.set_service_status("root", "docs-api", status)

    with pytest.raises(ValidationError, match=r"MCP service is not enabled .*log_id: call_") as search_exc:
        service.search(actor="alice", path="docs-api", query=None)
    with pytest.raises(ValidationError, match="MCP service is not enabled"):
        service.list_tools(actor="alice", service_key="docs-api")
    with pytest.raises(ValidationError, match=r"MCP service is not enabled .*log_id: call_") as execute_exc:
        asyncio.run(service.execute("alice", "docs-api", "search_docs", {"query": "hello"}))

    assert client.call_tool_calls == []
    logs = service.governance.list_logs(actor="root", status=CallLogStatus.error.value)
    search_log = next(log for log in logs if log["entrypoint"] == "metamcp_search")
    execute_log = next(log for log in logs if log["entrypoint"] == "metamcp_execute")
    assert search_log["log_id"] in str(search_exc.value)
    assert search_log["source_key"] == "docs-api"
    assert json.loads(search_log["request_json"]) == {
        "path": "docs-api",
        "query": None,
        "limit": 20,
        "profile_key": None,
    }
    assert json.loads(search_log["response_json"])["error"] == "MCP service is not enabled"
    assert search_log["error_message"] == "MCP service is not enabled"
    assert execute_log["log_id"] in str(execute_exc.value)
    assert execute_log["source_key"] == "docs-api"
    assert execute_log["tool_name"] == "search_docs"
    assert json.loads(execute_log["request_json"]) == {
        "service": "docs-api",
        "tool_name": "search_docs",
        "params": {"query": "hello"},
        "profile_key": None,
    }
    assert json.loads(execute_log["response_json"])["error"] == "MCP service is not enabled"
    assert execute_log["error_message"] == "MCP service is not enabled"


def test_sync_deactivates_removed_tools_and_hides_stale_tools(wm_paths: AgentBridgePaths) -> None:
    class ChangingMcpClient(FakeMcpClient):
        def __init__(self) -> None:
            super().__init__()
            self.tool_batches = [
                [
                    {
                        "name": "list_docs",
                        "description": "List documents",
                        "input_schema": {"type": "object", "properties": {}},
                        "annotations": {"readOnlyHint": True},
                    },
                    {
                        "name": "get_doc",
                        "description": "Get document detail",
                        "input_schema": {"type": "object", "properties": {"doc_id": {"type": "string"}}},
                        "annotations": {"readOnlyHint": True},
                    },
                ],
                [
                    {
                        "name": "list_docs",
                        "description": "List documents",
                        "input_schema": {"type": "object", "properties": {}},
                        "annotations": {"readOnlyHint": True},
                    }
                ],
            ]

        async def list_tools(
            self,
            endpoint_url: str,
            headers: dict[str, str],
            timeout: float = 150.0,
        ) -> list[dict[str, object]]:
            self.list_tools_calls.append({"endpoint_url": endpoint_url, "headers": headers, "timeout": timeout})
            return self.tool_batches.pop(0)

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    client = ChangingMcpClient()
    service = CapabilityService(store=store, mcp_client=client, admins={"root"})
    service.register_service("root", "docs-api", "Docs API", "https://example.test/mcp", {}, "Document capabilities", ["docs"])

    asyncio.run(service.sync_tools("root", "docs-api"))
    assert [tool["tool"] for tool in service.list_tools("alice", "docs-api")] == ["get_doc", "list_docs"]

    asyncio.run(service.sync_tools("root", "docs-api"))

    assert [tool["tool"] for tool in service.list_tools("alice", "docs-api")] == ["list_docs"]
    assert [item["tool"] for item in service.search("alice", "docs-api", None)["items"]] == ["list_docs"]
    with pytest.raises(NotFound, match=r"tool_not_found，可用的类似工具有：\[list_docs\].*log_id: call_"):
        asyncio.run(service.execute("alice", "docs-api", "get_doc", {"doc_id": "doc-1"}))


def test_execute_rejects_unconfigured_tool(wm_paths: AgentBridgePaths) -> None:
    service, client = _service(wm_paths)
    service.register_service("root", "docs-api", "Docs API", "https://example.test/mcp", {}, "Document capabilities", ["docs"])
    asyncio.run(service.sync_tools("root", "docs-api"))

    with pytest.raises(ValidationError, match=r"工具类型未配置.*log_id: call_") as exc_info:
        asyncio.run(service.execute("alice", "docs-api", "delete_doc", {"doc_id": "1"}))

    assert client.call_tool_calls == []
    logs = service.governance.list_logs(actor="root", status=CallLogStatus.blocked.value)
    assert logs[0]["log_id"] in str(exc_info.value)
    assert logs[0]["source_key"] == "docs-api"
    assert logs[0]["tool_name"] == "delete_doc"
    assert json.loads(logs[0]["request_json"]) == {
        "service": "docs-api",
        "tool_name": "delete_doc",
        "params": {"doc_id": "1"},
        "profile_key": None,
    }
    expected_error = "工具类型未配置，请联系管理员在 Agent Bridge 中配置工具类型"
    assert json.loads(logs[0]["response_json"])["error"] == expected_error
    assert logs[0]["error_message"] == expected_error


def test_admin_configures_tool_type_and_sync_preserves_choice(wm_paths: AgentBridgePaths) -> None:
    class ReadonlyNamingMcpClient(FakeMcpClient):
        async def list_tools(
            self,
            endpoint_url: str,
            headers: dict[str, str],
            timeout: float = 150.0,
        ) -> list[dict[str, object]]:
            return [
                {
                    "name": "list_archives",
                    "description": "List archives",
                    "input_schema": {"type": "object"},
                    "annotations": {"readOnlyHint": True},
                },
                {
                    "name": "get_archive_detail",
                    "description": "Get archive detail",
                    "input_schema": {"type": "object"},
                    "annotations": {"readOnlyHint": True},
                },
                {
                    "name": "delete_archive",
                    "description": "Delete archive metadata",
                    "input_schema": {"type": "object"},
                    "annotations": {"readOnlyHint": True},
                },
            ]

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    service = CapabilityService(store=store, mcp_client=ReadonlyNamingMcpClient(), admins={"root"})
    service.register_service("root", "docs-api", "Docs API", "https://example.test/mcp", {}, "Document capabilities", ["docs"])

    asyncio.run(service.sync_tools("root", "docs-api"))
    with pytest.raises(AccessDenied):
        service.set_tool_type("alice", "docs-api", "list_archives", ToolType.overview.value)

    configured = service.set_tool_type("root", "docs-api", "list_archives", ToolType.overview.value)
    service.set_tool_type("root", "docs-api", "get_archive_detail", ToolType.detail.value)

    tool_types = {tool["tool"]: tool["tool_type"] for tool in service.list_tools("alice", "docs-api")}
    assert configured["tool_type"] == ToolType.overview.value
    assert tool_types == {
        "delete_archive": ToolType.unconfigured.value,
        "get_archive_detail": ToolType.detail.value,
        "list_archives": ToolType.overview.value,
    }

    asyncio.run(service.sync_tools("root", "docs-api"))

    tool_types = {tool["tool"]: tool["tool_type"] for tool in service.list_tools("alice", "docs-api")}
    assert tool_types["list_archives"] == ToolType.overview.value
    assert tool_types["get_archive_detail"] == ToolType.detail.value
    assert tool_types["delete_archive"] == ToolType.unconfigured.value


def test_execute_rejects_unexpected_tool_type(wm_paths: AgentBridgePaths) -> None:
    service, _client = _service(wm_paths)
    service.register_service("root", "docs-api", "Docs API", "https://example.test/mcp", {}, "Document capabilities", ["docs"])
    service.store.upsert_mcp_tool(
        service_key="docs-api",
        tool_name="experimental_tool",
        display_name="Experimental Tool",
        description="Experimental capability",
        input_schema={"type": "object"},
        tool_type="experimental",
        tags=[],
        examples=[],
    )

    with pytest.raises(ValidationError, match="tool type is not executable"):
        asyncio.run(service.execute("alice", "docs-api", "experimental_tool", {}))


def test_execute_calls_readonly_tool(wm_paths: AgentBridgePaths) -> None:
    service, client = _service(wm_paths)
    service.register_service(
        "root",
        "docs-api",
        "Docs API",
        "https://example.test/mcp",
        {"Authorization": "Bearer token"},
        "Document capabilities",
        ["docs"],
    )
    asyncio.run(service.sync_tools("root", "docs-api"))
    service.set_tool_type("root", "docs-api", "search_docs", ToolType.search.value)

    result = asyncio.run(service.execute("alice", "docs-api", "search_docs", {"query": "hello"}))

    assert "service" not in result
    assert "tool" not in result
    assert "tool_name" not in result
    assert result["success"] is True
    assert result["result"] == {"is_error": False, "structured": {"rows": [{"id": 1}]}, "content": []}
    assert result["log_id"].startswith("call_")
    assert client.call_tool_calls == [
        {
            "endpoint_url": "https://example.test/mcp",
            "headers": {"Authorization": "Bearer token"},
            "tool_name": "search_docs",
            "params": {"query": "hello"},
            "timeout": 150.0,
        }
    ]


def test_search_root_filters_services_by_profile_and_returns_log_id(wm_paths: AgentBridgePaths) -> None:
    service, client = _service(wm_paths)
    service.register_service("root", "mysql", "MySQL", "https://mysql.test/mcp", {}, "SQL service", ["db"])
    service.register_service("root", "hive", "Hive", "https://hive.test/mcp", {}, "Hive service", ["db"])
    client.tools = [{"name": "query_sql", "description": "Run SQL", "input_schema": {"type": "object"}}]
    asyncio.run(service.sync_tools("root", "mysql"))
    asyncio.run(service.sync_tools("root", "hive"))
    service.governance.upsert_profile("root", "safe-readonly", "安全只读", "", "active")
    service.governance.replace_profile_rules(
        "root",
        "safe-readonly",
        [
            {"source_type": "mcp_service", "source_key": "mysql", "effect": "allow"},
            {"source_type": "mcp_service", "source_key": "hive", "effect": "deny"},
        ],
    )

    result = service.search("root", None, None, profile_key="safe-readonly")

    assert result["path"] == "/"
    assert [item["service"] for item in result["items"]] == ["mysql"]
    assert result["log_id"].startswith("call_")
    detail = service.governance.get_log(actor="root", log_id=result["log_id"])
    assert detail["entrypoint"] == "metamcp_search"
    assert json.loads(detail["request_json"]) == {
        "path": None,
        "query": None,
        "limit": 20,
        "profile_key": "safe-readonly",
    }
    assert [item["service"] for item in json.loads(detail["response_json"])["items"]] == ["mysql"]


def test_search_denied_service_returns_empty_items_and_log_id(wm_paths: AgentBridgePaths) -> None:
    service, client = _service(wm_paths)
    service.register_service("root", "mysql", "MySQL", "https://mysql.test/mcp", {}, "SQL service", ["db"])
    service.register_service("root", "hive", "Hive", "https://hive.test/mcp", {}, "Hive service", ["db"])
    client.tools = [{"name": "query_sql", "description": "Run SQL", "input_schema": {"type": "object"}}]
    asyncio.run(service.sync_tools("root", "mysql"))
    asyncio.run(service.sync_tools("root", "hive"))
    service.governance.upsert_profile("root", "safe-readonly", "安全只读", "", "active")
    service.governance.replace_profile_rules(
        "root",
        "safe-readonly",
        [{"source_type": "mcp_service", "source_key": "hive", "effect": "deny"}],
    )

    result = service.search("root", "hive", None, profile_key="safe-readonly")

    assert result["path"] == "hive"
    assert result["items"] == []
    assert result["log_id"].startswith("call_")
    detail = service.governance.get_log(actor="root", log_id=result["log_id"])
    assert detail["status"] == CallLogStatus.success.value
    assert json.loads(detail["response_json"]) == {"path": "hive", "items": []}


def test_execute_blocked_by_profile_writes_log_and_does_not_call_client(wm_paths: AgentBridgePaths) -> None:
    service, client = _service(wm_paths)
    service.register_service("root", "hive", "Hive", "https://hive.test/mcp", {}, "Hive service", ["db"])
    client.tools = [{"name": "query_sql", "description": "Run SQL", "input_schema": {"type": "object"}}]
    asyncio.run(service.sync_tools("root", "hive"))
    service.governance.upsert_profile("root", "safe-readonly", "安全只读", "", "active")
    service.governance.replace_profile_rules(
        "root",
        "safe-readonly",
        [{"source_type": "mcp_service", "source_key": "hive", "effect": "deny"}],
    )

    with pytest.raises(ValidationError, match=r"source is blocked by profile policy .*log_id: call_") as exc_info:
        asyncio.run(service.execute("root", "hive", "query_sql", {"sql": "select 1"}, profile_key="safe-readonly"))

    assert client.call_tool_calls == []
    logs = service.governance.list_logs(actor="root", status=CallLogStatus.blocked.value)
    assert len(logs) == 1
    assert logs[0]["log_id"] in str(exc_info.value)
    assert logs[0]["source_key"] == "hive"
    assert logs[0]["tool_name"] == "query_sql"
    assert json.loads(logs[0]["request_json"]) == {
        "service": "hive",
        "tool_name": "query_sql",
        "params": {"sql": "select 1"},
        "profile_key": "safe-readonly",
    }


def test_execute_success_returns_log_id_and_metamcp_execute_log(wm_paths: AgentBridgePaths) -> None:
    service, client = _service(wm_paths)
    service.register_service("root", "mysql", "MySQL", "https://mysql.test/mcp", {}, "SQL service", ["db"])
    client.tools = [{"name": "query_sql", "description": "Run SQL", "input_schema": {"type": "object"}}]
    client.call_result = {"structured": {"rows": [{"id": 1}]}, "is_error": False, "content": []}
    asyncio.run(service.sync_tools("root", "mysql"))
    service.set_tool_type("root", "mysql", "query_sql", ToolType.search.value)

    result = asyncio.run(service.execute("root", "mysql", "query_sql", {"sql": "select 1"}))

    assert result["success"] is True
    assert result["log_id"].startswith("call_")
    detail = service.governance.get_log(actor="root", log_id=result["log_id"])
    assert detail["entrypoint"] == "metamcp_execute"
    assert detail["status"] == CallLogStatus.success.value
    assert json.loads(detail["request_json"]) == {
        "service": "mysql",
        "tool_name": "query_sql",
        "params": {"sql": "select 1"},
        "profile_key": None,
    }
    assert json.loads(detail["response_json"])["result"] == client.call_result


def test_search_error_message_includes_log_id(wm_paths: AgentBridgePaths) -> None:
    service, _client = _service(wm_paths)

    with pytest.raises(NotFound, match=r"profile not found .*log_id: call_") as exc_info:
        service.search("root", None, None, profile_key="missing")

    logs = service.governance.list_logs(actor="root", status=CallLogStatus.error.value)
    assert len(logs) == 1
    assert logs[0]["log_id"] in str(exc_info.value)
    assert logs[0]["entrypoint"] == "metamcp_search"


def test_execute_wraps_mcp_client_failures(wm_paths: AgentBridgePaths) -> None:
    class FailingCallMcpClient(FakeMcpClient):
        async def call_tool(
            self,
            endpoint_url: str,
            headers: dict[str, str],
            tool_name: str,
            arguments: dict[str, object],
            timeout: float = 150.0,
        ) -> dict[str, object]:
            raise RuntimeError("transport unavailable")

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    service = CapabilityService(store=store, mcp_client=FailingCallMcpClient(), admins={"root"})
    service.register_service("root", "docs-api", "Docs API", "https://example.test/mcp", {}, "Document capabilities", ["docs"])
    asyncio.run(service.sync_tools("root", "docs-api"))
    service.set_tool_type("root", "docs-api", "search_docs", ToolType.search.value)

    with pytest.raises(ValidationError, match=r"MCP tool execution failed: RuntimeError: transport unavailable .*log_id: call_") as exc_info:
        asyncio.run(service.execute("alice", "docs-api", "search_docs", {"query": "hello"}))

    logs = service.governance.list_logs(actor="root", status=CallLogStatus.error.value)
    assert len(logs) == 1
    assert logs[0]["log_id"] in str(exc_info.value)
    assert logs[0]["source_key"] == "docs-api"
    assert logs[0]["tool_name"] == "search_docs"
    assert json.loads(logs[0]["request_json"]) == {
        "service": "docs-api",
        "tool_name": "search_docs",
        "params": {"query": "hello"},
        "profile_key": None,
    }
    assert json.loads(logs[0]["response_json"])["error"] == "MCP tool execution failed: RuntimeError: transport unavailable"
    assert logs[0]["error_message"] == "MCP tool execution failed: RuntimeError: transport unavailable"


def test_execute_unwraps_exceptiongroup_from_mcp_client(wm_paths: AgentBridgePaths) -> None:
    # streamablehttp_client 抛 BaseExceptionGroup("unhandled errors in a taskgroup ...") 时,
    # 错误消息必须展开到真正的子异常, 而不是保留 "unhandled errors in a taskgroup".
    class TaskGroupFailingMcpClient(FakeMcpClient):
        async def call_tool(
            self,
            endpoint_url: str,
            headers: dict[str, str],
            tool_name: str,
            arguments: dict[str, object],
            timeout: float = 150.0,
        ) -> dict[str, object]:
            raise ExceptionGroup(
                "unhandled errors in a taskgroup",
                [ConnectionRefusedError("[Errno 111] Connection refused -> http://internal-mcp:8080/mcp")],
            )

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    service = CapabilityService(store=store, mcp_client=TaskGroupFailingMcpClient(), admins={"root"})
    service.register_service("root", "docs-api", "Docs API", "https://example.test/mcp", {}, "Document capabilities", ["docs"])
    asyncio.run(service.sync_tools("root", "docs-api"))
    service.set_tool_type("root", "docs-api", "search_docs", ToolType.search.value)

    with pytest.raises(ValidationError, match=r"MCP tool execution failed: ConnectionRefusedError: .*Connection refused.*log_id: call_") as exc_info:
        asyncio.run(service.execute("alice", "docs-api", "search_docs", {"query": "hello"}))

    message = str(exc_info.value)
    assert "unhandled errors in a taskgroup" not in message
    assert "ConnectionRefusedError" in message

    logs = service.governance.list_logs(actor="root", status=CallLogStatus.error.value)
    assert len(logs) == 1
    assert "unhandled errors in a taskgroup" not in logs[0]["error_message"]
    assert "ConnectionRefusedError" in logs[0]["error_message"]


def test_execute_requires_existing_service_and_tool(wm_paths: AgentBridgePaths) -> None:
    service, _client = _service(wm_paths)
    service.register_service("root", "docs-api", "Docs API", "https://example.test/mcp", {}, "Document capabilities", ["docs"])
    service.register_service("root", "docs-admin", "Docs Admin", "https://example.test/admin", {}, "Admin document capabilities", ["docs", "admin"])
    service.register_service("root", "docs-archive", "Docs Archive", "https://example.test/archive", {}, "Archive document capabilities", ["docs", "archive"])
    service.register_service("root", "reports-api", "Reports API", "https://example.test/reports", {}, "Report capabilities", ["reports"])
    asyncio.run(service.sync_tools("root", "docs-api"))
    service.store.upsert_mcp_tool(
        service_key="docs-api",
        tool_name="search_documents",
        display_name="search_documents",
        description="Search documents with advanced filters",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        tool_type=ToolType.search.value,
        tags=["docs"],
        examples=[],
    )
    service.store.upsert_mcp_tool(
        service_key="docs-api",
        tool_name="research_docs",
        display_name="research_docs",
        description="Research document corpus",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        tool_type=ToolType.search.value,
        tags=["docs"],
        examples=[],
    )

    with pytest.raises(NotFound, match=r"service_not_found，可用的类似服务有：\[docs-api, docs-admin, docs-archive\].*log_id: call_") as missing_service_exc:
        asyncio.run(service.execute("alice", "docs-ap", "search_docs", {}))
    with pytest.raises(NotFound, match=r"tool_not_found，可用的类似工具有：\[search_docs, search_documents, research_docs\].*log_id: call_") as missing_tool_exc:
        asyncio.run(service.execute("alice", "docs-api", "search_doc", {}))

    logs = service.governance.list_logs(actor="root", status=CallLogStatus.error.value)
    missing_service_log = next(log for log in logs if log["source_key"] == "docs-ap")
    missing_tool_log = next(log for log in logs if log["source_key"] == "docs-api" and log["tool_name"] == "search_doc")
    assert missing_service_log["log_id"] in str(missing_service_exc.value)
    assert missing_service_log["source_key"] == "docs-ap"
    assert missing_service_log["tool_name"] == "search_docs"
    assert json.loads(missing_service_log["request_json"]) == {
        "service": "docs-ap",
        "tool_name": "search_docs",
        "params": {},
        "profile_key": None,
    }
    expected_service_error = (
        "service_not_found，可用的类似服务有：[docs-api, docs-admin, docs-archive]。"
        "请使用 search() 查看当前可用服务及工具的详细说明。"
    )
    assert json.loads(missing_service_log["response_json"])["error"] == expected_service_error
    assert missing_service_log["error_message"] == expected_service_error
    assert missing_tool_log["log_id"] in str(missing_tool_exc.value)
    assert missing_tool_log["source_key"] == "docs-api"
    assert missing_tool_log["tool_name"] == "search_doc"
    assert json.loads(missing_tool_log["request_json"]) == {
        "service": "docs-api",
        "tool_name": "search_doc",
        "params": {},
        "profile_key": None,
    }
    expected_tool_error = (
        "tool_not_found，可用的类似工具有：[search_docs, search_documents, research_docs]。"
        "请使用 search(path='docs-api') 查看该服务下工具及参数的详细说明。"
    )
    assert json.loads(missing_tool_log["response_json"])["error"] == expected_tool_error
    assert missing_tool_log["error_message"] == expected_tool_error


def test_execute_missing_tool_suggestions_stay_within_same_service_and_fall_back_cleanly(wm_paths: AgentBridgePaths) -> None:
    service, _client = _service(wm_paths)
    service.register_service("root", "docs-api", "Docs API", "https://example.test/mcp", {}, "Document capabilities", ["docs"])
    service.register_service("root", "reports-api", "Reports API", "https://example.test/reports", {}, "Report capabilities", ["reports"])
    asyncio.run(service.sync_tools("root", "docs-api"))
    asyncio.run(service.sync_tools("root", "reports-api"))
    service.store.upsert_mcp_tool(
        service_key="reports-api",
        tool_name="search_reports",
        display_name="search_reports",
        description="Search reports",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        tool_type=ToolType.search.value,
        tags=["reports"],
        examples=[],
    )

    with pytest.raises(NotFound, match=r"tool_not_found，可用的类似工具有：\[search_docs, delete_doc\].*log_id: call_") as same_service_exc:
        asyncio.run(service.execute("alice", "docs-api", "search_doc", {}))
    with pytest.raises(NotFound, match=r"tool_not_found.*log_id: call_") as no_match_exc:
        asyncio.run(service.execute("alice", "docs-api", "zzzzzz", {}))

    assert "search_reports" not in str(same_service_exc.value)
    assert "可用的类似工具有" not in str(no_match_exc.value)
    assert "search(path='docs-api')" in str(no_match_exc.value)
