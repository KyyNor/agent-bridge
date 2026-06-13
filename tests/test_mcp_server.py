from __future__ import annotations

import asyncio
import json


def test_mcp_server_exposes_search_and_execute_tools():
    from agent_bridge.capabilities.mcp_server import create_mcp_server

    class FakeService:
        capabilities = None

    mcp = create_mcp_server(FakeService())
    tools = asyncio.run(mcp.list_tools())
    tool_names = [tool.name for tool in tools]
    assert tool_names == ["search", "execute"]


def test_mcp_search_tool_has_path_query_schema():
    from agent_bridge.capabilities.mcp_server import create_mcp_server

    class FakeService:
        capabilities = None

    mcp = create_mcp_server(FakeService())
    tools = asyncio.run(mcp.list_tools())
    tools_by_name = {t.name: t for t in tools}
    search_tool = tools_by_name["search"]
    schema = search_tool.inputSchema
    assert "path" in schema["properties"]
    assert "query" in schema["properties"]
    assert "limit" in schema["properties"]
    assert "no arguments" in search_tool.description


def test_mcp_execute_tool_has_service_key_tool_arguments_schema():
    from agent_bridge.capabilities.mcp_server import create_mcp_server

    class FakeService:
        capabilities = None

    mcp = create_mcp_server(FakeService())
    tools = asyncio.run(mcp.list_tools())
    tools_by_name = {t.name: t for t in tools}
    execute_tool = tools_by_name["execute"]
    schema = execute_tool.inputSchema
    assert "service_key" in schema["properties"]
    assert "tool" in schema["properties"]
    assert "arguments" in schema["properties"]


def test_mcp_search_tool_calls_capability_service():
    from agent_bridge.capabilities.mcp_server import create_mcp_server

    returned = {"items": [{"service": "svc-1", "tool": "read"}], "path": "/", "log_id": "call_1"}

    class FakeCapabilities:
        def search(self, *, actor, path, query, limit=20, profile_key=None):
            assert path == "svc-1"
            assert query == "read"
            assert limit == 3
            return returned

    class FakeService:
        capabilities = FakeCapabilities()

    mcp = create_mcp_server(FakeService())
    content, structured = asyncio.run(mcp.call_tool("search", {"path": "svc-1", "query": "read", "limit": 3}))
    assert structured == returned


def test_mcp_execute_tool_calls_capability_service():
    from agent_bridge.capabilities.mcp_server import create_mcp_server

    returned = {"success": True, "result": {}, "service": "svc-1", "tool": "read", "log_id": "call_1"}

    class FakeCapabilities:
        async def execute(self, *, actor, service, tool, arguments, profile_key=None):
            assert service == "svc-1"
            assert tool == "read"
            assert arguments == {"path": "/docs"}
            return returned

    class FakeService:
        capabilities = FakeCapabilities()

    mcp = create_mcp_server(FakeService())
    content, structured = asyncio.run(mcp.call_tool("execute", {"service_key": "svc-1", "tool": "read", "arguments": {"path": "/docs"}}))
    assert structured == returned


def test_mcp_pinned_tool_calls_original_service_tool():
    from agent_bridge.capabilities.mcp_server import create_mcp_server

    returned = {"success": True, "result": {"rows": []}, "service": "mysql", "tool": "query_users"}
    calls = []

    class FakeCapabilities:
        def pinned_tool_specs(self, actor, profile_key):
            assert profile_key == "safe-readonly"
            return [
                {
                    "generated_tool_name": "pin_mysql_query_users",
                    "service_key": "mysql",
                    "service_name": "MySQL",
                    "tool_name": "query_users",
                    "tool_type": "search",
                    "source": "manual",
                    "description": "Pinned query users",
                    "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
                }
            ]

        async def execute(self, *, actor, service, tool, arguments, profile_key=None):
            calls.append(
                {
                    "service": service,
                    "tool": tool,
                    "arguments": arguments,
                    "profile_key": profile_key,
                }
            )
            return returned

    class FakeService:
        capabilities = FakeCapabilities()

    mcp = create_mcp_server(FakeService(), profile_key="safe-readonly")
    content, structured = asyncio.run(mcp.call_tool("pin_mysql_query_users", {"q": "alice"}))

    assert structured == returned
    assert calls == [
        {
            "service": "mysql",
            "tool": "query_users",
            "arguments": {"q": "alice"},
            "profile_key": "safe-readonly",
        }
    ]


def test_mcp_skips_pinned_tool_with_invalid_schema_field(caplog):
    from agent_bridge.capabilities.mcp_server import create_mcp_server

    class FakeCapabilities:
        def pinned_tool_specs(self, actor, profile_key):
            return [
                {
                    "generated_tool_name": "pin_mysql_query_users",
                    "service_key": "mysql",
                    "service_name": "MySQL",
                    "tool_name": "query_users",
                    "tool_type": "search",
                    "source": "manual",
                    "description": "Pinned query users",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "class": {"type": "string"},
                            "user-id": {"type": "string"},
                        },
                    },
                }
            ]

        async def execute(self, *, actor, service, tool, arguments, profile_key=None):
            raise AssertionError("invalid pinned tool should not be registered")

    class FakeService:
        capabilities = FakeCapabilities()

    caplog.set_level("WARNING", logger="agent_bridge.mcp")
    mcp = create_mcp_server(FakeService(), profile_key="safe-readonly")
    tools = asyncio.run(mcp.list_tools())

    assert [tool.name for tool in tools] == ["search", "execute"]
    assert "pin_mysql_query_users" in caplog.text
    assert "class" in caplog.text
    assert "user-id" in caplog.text


def test_mcp_search_with_default_service_initializes_schema(wm_paths):
    from agent_bridge.capabilities.mcp_server import create_mcp_server
    from agent_bridge.knowledge.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    mcp = create_mcp_server(svc)
    content, structured = asyncio.run(mcp.call_tool("search", {}))
    assert structured["path"] == "/"
    assert structured["items"] == [
        {
            "kind": "builtin",
            "service": "wiki",
            "name": "Wiki",
            "description": "内置知识库查询能力",
            "tags": ["builtin", "knowledge"],
            "tool_count": 5,
            "status": "enabled",
            "resources": [],
        },
        {
            "kind": "builtin",
            "service": "codegraph",
            "name": "CodeGraph",
            "description": "内置代码仓库结构和代码查询能力",
            "tags": ["builtin", "code"],
            "tool_count": 1,
            "status": "enabled",
            "resources": [],
        },
    ]
    assert structured["log_id"].startswith("call_")
