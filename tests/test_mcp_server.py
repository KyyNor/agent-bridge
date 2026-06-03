from __future__ import annotations

import asyncio

import pytest

from mcp.types import CallToolRequest, CallToolRequestParams, ListToolsRequest


def _get_tools_sync(server):
    """Call the registered list_tools handler and return Tool objects."""
    handler = server.request_handlers[ListToolsRequest]
    result = asyncio.run(handler(ListToolsRequest(method="tools/list")))
    return result.root.tools


def test_mcp_server_exposes_search_and_execute_tools():
    from wiki_manager.mcp_server import create_mcp_server

    server = create_mcp_server()
    tools = _get_tools_sync(server)
    tool_names = [tool.name for tool in tools]
    assert tool_names == ["search", "execute"]


def test_mcp_search_tool_has_path_query_schema():
    from wiki_manager.mcp_server import create_mcp_server

    server = create_mcp_server()
    tools = _get_tools_sync(server)
    tools_by_name = {t.name: t for t in tools}
    search_tool = tools_by_name["search"]
    schema = search_tool.inputSchema
    assert "path" in schema["properties"]
    assert "query" in schema["properties"]
    assert "limit" in schema["properties"]
    assert "required" not in schema


def test_mcp_execute_tool_has_service_tool_arguments_schema():
    from wiki_manager.mcp_server import create_mcp_server

    server = create_mcp_server()
    tools = _get_tools_sync(server)
    tools_by_name = {t.name: t for t in tools}
    execute_tool = tools_by_name["execute"]
    schema = execute_tool.inputSchema
    assert schema["required"] == ["service", "tool", "arguments"]
    assert "service" in schema["properties"]
    assert "tool" in schema["properties"]
    assert "arguments" in schema["properties"]


def test_mcp_search_tool_calls_capability_service():
    from wiki_manager.mcp_server import create_mcp_server

    returned = {"items": [{"service": "svc-1", "tool": "read"}]}

    class FakeCapabilities:
        def search(self, *, actor, path, query, limit=20):
            assert actor == "root"
            assert path == "svc-1"
            assert query == "read"
            assert limit == 3
            return returned

    class FakeService:
        capabilities = FakeCapabilities()

    server = create_mcp_server(service=FakeService(), actor="root")
    handler = server.request_handlers[CallToolRequest]
    result = asyncio.run(handler(CallToolRequest(
        params=CallToolRequestParams(
            name="search",
            arguments={
                "path": "svc-1",
                "query": "read",
                "limit": 3,
            },
        )
    )))

    payload = result.root.structuredContent
    assert payload["items"][0] == returned["items"][0]


def test_mcp_execute_tool_calls_capability_service():
    from wiki_manager.mcp_server import create_mcp_server

    returned = {"success": True, "content": [{"type": "text", "text": "ok"}]}

    class FakeCapabilities:
        async def execute(self, *, actor, service, tool, arguments):
            assert actor == "root"
            assert service == "svc-1"
            assert tool == "read"
            assert arguments == {"path": "/docs"}
            return returned

    class FakeService:
        capabilities = FakeCapabilities()

    server = create_mcp_server(service=FakeService(), actor="root")
    handler = server.request_handlers[CallToolRequest]
    result = asyncio.run(handler(CallToolRequest(
        params=CallToolRequestParams(
            name="execute",
            arguments={
                "service": "svc-1",
                "tool": "read",
                "arguments": {"path": "/docs"},
            },
        )
    )))

    payload = result.root.structuredContent
    assert payload["success"] is True
