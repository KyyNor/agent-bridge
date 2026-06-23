from __future__ import annotations

import asyncio
import json


def test_mcp_server_exposes_search_and_execute_tools():
    from agent_bridge.capability_hub.gateway.metamcp import create_mcp_server

    class FakeService:
        capabilities = None

    mcp = create_mcp_server(FakeService())
    tools = asyncio.run(mcp.list_tools())
    tool_names = [tool.name for tool in tools]
    assert tool_names == ["search", "execute", "artifacts_search"]


def test_mcp_search_tool_has_path_query_schema():
    from agent_bridge.capability_hub.gateway.metamcp import create_mcp_server

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


def test_mcp_execute_tool_has_service_tool_name_params_schema():
    from agent_bridge.capability_hub.gateway.metamcp import create_mcp_server

    class FakeService:
        capabilities = None

    mcp = create_mcp_server(FakeService())
    tools = asyncio.run(mcp.list_tools())
    tools_by_name = {t.name: t for t in tools}
    execute_tool = tools_by_name["execute"]
    schema = execute_tool.inputSchema
    assert "service" in schema["properties"]
    assert "tool_name" in schema["properties"]
    assert "params" in schema["properties"]


def test_mcp_search_tool_calls_capability_service():
    from agent_bridge.capability_hub.gateway.metamcp import create_mcp_server

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
    from agent_bridge.capability_hub.gateway.metamcp import create_mcp_server

    returned = {"success": True, "result": {}, "service": "svc-1", "tool_name": "read", "log_id": "call_1"}

    class FakeCapabilities:
        async def execute(self, *, actor, service, tool_name, params, profile_key=None, workflow_context=None):
            assert service == "svc-1"
            assert tool_name == "read"
            assert params == {"path": "/docs"}
            assert workflow_context is None
            return returned

    class FakeService:
        capabilities = FakeCapabilities()

    mcp = create_mcp_server(FakeService())
    content, structured = asyncio.run(mcp.call_tool("execute", {"service": "svc-1", "tool_name": "read", "params": {"path": "/docs"}}))
    assert structured == returned


def test_mcp_exposes_builtin_direct_tools_at_top_level():
    from agent_bridge.capability_hub.gateway.metamcp import create_mcp_server
    from agent_bridge.capability_hub.sources.builtin.base import BuiltinTool

    class FakeProvider:
        def __init__(self, source_key, tools):
            self.source_key = source_key
            self.tools = tools

        def list_tools(self, actor, profile_key):
            return self.tools

    class FakeCapabilities:
        builtin_providers = {
            "wiki": FakeProvider(
                "wiki",
                [
                    BuiltinTool(
                        "search",
                        "Wiki Search",
                        "Search snippets in an allowed KB.",
                        {"type": "object", "properties": {"kb": {"type": "string"}, "question": {"type": "string"}}},
                        "search",
                    ),
                    BuiltinTool(
                        "ask",
                        "Wiki Ask",
                        "Ask a question against an allowed KB.",
                        {"type": "object", "properties": {"kb": {"type": "string"}, "question": {"type": "string"}}},
                        "search",
                    ),
                ],
            ),
            "codegraph": FakeProvider(
                "codegraph",
                [
                    BuiltinTool(
                        "codegraph_explore",
                        "CodeGraph Explore",
                        "Explore an allowed code repository.",
                        {"type": "object", "properties": {"repo": {"type": "string"}, "query": {"type": "string"}}},
                        "search",
                    )
                ],
            ),
        }

    class FakeService:
        capabilities = FakeCapabilities()

    mcp = create_mcp_server(FakeService())
    tools = asyncio.run(mcp.list_tools())
    tool_names = [tool.name for tool in tools]

    assert "wiki_search" in tool_names
    assert "wiki_ask" in tool_names
    assert "codegraph_explore" in tool_names
    assert tools[tool_names.index("wiki_search")].inputSchema["properties"]["kb"]["type"] == "string"
    assert tools[tool_names.index("codegraph_explore")].inputSchema["properties"]["repo"]["type"] == "string"


def test_mcp_exposes_memory_direct_tools_at_top_level():
    from agent_bridge.capability_hub.gateway.metamcp import create_mcp_server
    from agent_bridge.capability_hub.sources.builtin.base import BuiltinTool

    class FakeProvider:
        source_key = "memory"

        def list_tools(self, actor, profile_key):
            return [
                BuiltinTool(
                    "search",
                    "Memory Search",
                    "Search active memory block.",
                    {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}},
                    "search",
                ),
                BuiltinTool(
                    "timeline",
                    "Memory Timeline",
                    "Read active memory timeline.",
                    {"type": "object", "properties": {"limit": {"type": "integer"}}},
                    "search",
                ),
                BuiltinTool(
                    "get",
                    "Memory Get",
                    "Read memory observation.",
                    {"type": "object", "properties": {"id": {"type": "string"}}},
                    "detail",
                ),
            ]

    class FakeCapabilities:
        builtin_providers = {"memory": FakeProvider()}

        async def execute(self, *, actor, service, tool_name, params, profile_key=None, workflow_context=None):
            return {"success": True, "service": service, "tool_name": tool_name, "result": params}

    class FakeService:
        capabilities = FakeCapabilities()

    mcp = create_mcp_server(FakeService(), profile_key="dev")
    names = [tool.name for tool in asyncio.run(mcp.list_tools())]

    assert "memory_search" in names
    assert "memory_timeline" in names
    assert "memory_get" in names


def test_mcp_builtin_direct_tool_calls_original_builtin_tool():
    from agent_bridge.capability_hub.gateway.metamcp import create_mcp_server
    from agent_bridge.capability_hub.sources.builtin.base import BuiltinTool

    calls = []
    returned = {"success": True, "result": {"answer": "ok"}, "service": "wiki", "tool_name": "ask"}

    class FakeProvider:
        source_key = "wiki"

        def list_tools(self, actor, profile_key):
            return [
                BuiltinTool(
                    "ask",
                    "Wiki Ask",
                    "Ask a question against an allowed KB.",
                    {"type": "object", "properties": {"kb": {"type": "string"}, "question": {"type": "string"}}},
                    "search",
                )
            ]

    class FakeCapabilities:
        builtin_providers = {"wiki": FakeProvider()}

        async def execute(self, *, actor, service, tool_name, params, profile_key=None, workflow_context=None):
            calls.append(
                {
                    "service": service,
                    "tool_name": tool_name,
                    "params": params,
                    "profile_key": profile_key,
                    "workflow_context": workflow_context,
                }
            )
            return returned

    class FakeService:
        capabilities = FakeCapabilities()

    mcp = create_mcp_server(FakeService(), profile_key="safe-readonly")
    content, structured = asyncio.run(mcp.call_tool("wiki_ask", {"kb": "frontend-docs", "question": "css"}))

    assert structured == returned
    assert calls == [
        {
            "service": "wiki",
            "tool_name": "ask",
            "params": {"kb": "frontend-docs", "question": "css"},
            "profile_key": "safe-readonly",
            "workflow_context": None,
        }
    ]


def test_mcp_pinned_tool_calls_original_service_tool():
    from agent_bridge.capability_hub.gateway.metamcp import create_mcp_server

    returned = {"success": True, "result": {"rows": []}, "service": "mysql", "tool_name": "query_users"}
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

        async def execute(self, *, actor, service, tool_name, params, profile_key=None, workflow_context=None):
            calls.append(
                {
                    "service": service,
                    "tool_name": tool_name,
                    "params": params,
                    "profile_key": profile_key,
                    "workflow_context": workflow_context,
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
            "tool_name": "query_users",
            "params": {"q": "alice"},
            "profile_key": "safe-readonly",
            "workflow_context": None,
        }
    ]


def test_mcp_skips_pinned_tool_with_invalid_schema_field(caplog):
    from agent_bridge.capability_hub.gateway.metamcp import create_mcp_server

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

        async def execute(self, *, actor, service, tool_name, params, profile_key=None, workflow_context=None):
            raise AssertionError("invalid pinned tool should not be registered")

    class FakeService:
        capabilities = FakeCapabilities()

    caplog.set_level("WARNING", logger="agent_bridge.mcp")
    mcp = create_mcp_server(FakeService(), profile_key="safe-readonly")
    tools = asyncio.run(mcp.list_tools())

    assert [tool.name for tool in tools] == ["search", "execute", "artifacts_search"]
    assert "pin_mysql_query_users" in caplog.text
    assert "class" in caplog.text
    assert "user-id" in caplog.text


def test_mcp_search_with_default_service_initializes_schema(wm_paths):
    from agent_bridge.capability_hub.gateway.metamcp import create_mcp_server
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    mcp = create_mcp_server(svc)
    content, structured = asyncio.run(mcp.call_tool("search", {}))
    assert structured["path"] == "/"
    assert structured["items"] == [
        {
            "kind": "builtin",
            "service": "built-in",
            "name": "Built-in",
            "description": "平台内置辅助工具",
            "tags": ["builtin", "platform"],
            "tool_count": 2,
            "status": "enabled",
            "resources": [],
        },
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
        {
            "kind": "builtin",
            "service": "memory",
            "name": "Memory",
            "description": "内置记忆检索能力",
            "tags": ["builtin", "memory"],
            "tool_count": 3,
            "status": "enabled",
            "resources": [],
        },
    ]
    assert structured["log_id"].startswith("call_")
