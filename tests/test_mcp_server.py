from __future__ import annotations

import asyncio

import pytest

from mcp.types import ListToolsRequest


def _get_tools_sync(server):
    """Call the registered list_tools handler and return Tool objects."""
    handler = server.request_handlers[ListToolsRequest]
    result = asyncio.run(handler(ListToolsRequest(method="tools/list")))
    return result.root.tools


def test_mcp_server_exposes_search_and_ask_tools():
    from wiki_manager.mcp_server import create_mcp_server

    server = create_mcp_server()
    tools = _get_tools_sync(server)
    tool_names = [tool.name for tool in tools]
    assert "search" in tool_names
    assert "ask" in tool_names


def test_mcp_search_tool_has_expected_schema():
    from wiki_manager.mcp_server import create_mcp_server

    server = create_mcp_server()
    tools = _get_tools_sync(server)
    tools_by_name = {t.name: t for t in tools}
    search_tool = tools_by_name["search"]
    schema = search_tool.inputSchema
    assert "kb_slug" in schema["properties"]
    assert "question" in schema["properties"]


def test_mcp_ask_tool_has_expected_schema():
    from wiki_manager.mcp_server import create_mcp_server

    server = create_mcp_server()
    tools = _get_tools_sync(server)
    tools_by_name = {t.name: t for t in tools}
    ask_tool = tools_by_name["ask"]
    schema = ask_tool.inputSchema
    assert "kb_slug" in schema["properties"]
    assert "question" in schema["properties"]
