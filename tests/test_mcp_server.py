from __future__ import annotations

import asyncio

import pytest

from mcp.types import CallToolRequest, CallToolRequestParams, ListToolsRequest


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


def test_mcp_search_tool_calls_service():
    from wiki_manager.domain import RetrievalResult
    from wiki_manager.mcp_server import create_mcp_server

    class FakeService:
        def search(self, actor, kb_slug, question, *, backend_slug=None, top_k=6):
            assert actor == "root"
            assert kb_slug == "frontend-docs"
            assert question == "auth"
            assert backend_slug == "ragflow"
            assert top_k == 3
            return [
                RetrievalResult(
                    chunk_id="chunk-1",
                    content="Authentication guide",
                    document_name="auth.md",
                    similarity=0.9,
                    dataset_id="ds-1",
                )
            ]

    server = create_mcp_server(service=FakeService(), actor="root")
    handler = server.request_handlers[CallToolRequest]
    result = asyncio.run(handler(CallToolRequest(
        params=CallToolRequestParams(
            name="search",
            arguments={
                "kb_slug": "frontend-docs",
                "question": "auth",
                "backend": "ragflow",
                "top_k": 3,
            },
        )
    )))

    payload = result.root.structuredContent
    assert payload["results"][0]["chunk_id"] == "chunk-1"


def test_mcp_ask_tool_calls_service():
    from wiki_manager.domain import AskResult
    from wiki_manager.mcp_server import create_mcp_server

    class FakeService:
        def ask(self, actor, kb_slug, question, *, backend_slug=None, session_id=None):
            assert actor == "root"
            assert kb_slug == "frontend-docs"
            assert question == "how auth works?"
            assert backend_slug == "ragflow"
            assert session_id == "sess-1"
            return AskResult(answer="Use SSO.", chunks=[], session_id="sess-1")

    server = create_mcp_server(service=FakeService(), actor="root")
    handler = server.request_handlers[CallToolRequest]
    result = asyncio.run(handler(CallToolRequest(
        params=CallToolRequestParams(
            name="ask",
            arguments={
                "kb_slug": "frontend-docs",
                "question": "how auth works?",
                "backend": "ragflow",
                "session_id": "sess-1",
            },
        )
    )))

    payload = result.root.structuredContent
    assert payload["answer"] == "Use SSO."
    assert payload["session_id"] == "sess-1"
