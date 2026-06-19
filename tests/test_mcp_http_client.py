from __future__ import annotations

import asyncio
from types import SimpleNamespace

from mcp.types import TextContent, Tool

from agent_bridge.capability_hub.sources.mcp.http_client import (
    McpHttpClient,
    normalize_call_tool_result,
    normalize_tool,
)


def test_normalize_tool_returns_plain_dict() -> None:
    tool = Tool(
        name="query_sql",
        description="Run SQL",
        inputSchema={
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
        },
    )

    assert normalize_tool(tool) == {
        "name": "query_sql",
        "description": "Run SQL",
        "input_schema": {
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
        },
        "annotations": None,
        "output_schema": None,
    }


def test_normalize_tool_preserves_metadata_as_plain_json() -> None:
    tool = SimpleNamespace(
        name="query_docs",
        description=None,
        inputSchema={"type": "object", "properties": {}},
        annotations=SimpleNamespace(
            model_dump=lambda **_kwargs: {"readOnlyHint": True, "title": "Query Docs"}
        ),
        outputSchema={
            "type": "object",
            "properties": {"rows": {"type": "array"}},
        },
    )

    assert normalize_tool(tool) == {
        "name": "query_docs",
        "description": "",
        "input_schema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True, "title": "Query Docs"},
        "output_schema": {
            "type": "object",
            "properties": {"rows": {"type": "array"}},
        },
    }


def test_mcp_http_client_list_tools_handles_pagination(monkeypatch) -> None:
    session_state = {
        "initialize_calls": 0,
        "list_tools_cursors": [],
    }

    class FakeStreamContext:
        async def __aenter__(self):
            return "read", "write", lambda: "session-id"

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    class FakeClientSession:
        def __init__(self, read_stream, write_stream, *, read_timeout_seconds):
            self.read_stream = read_stream
            self.write_stream = write_stream
            self.read_timeout_seconds = read_timeout_seconds

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def initialize(self):
            session_state["initialize_calls"] += 1

        async def list_tools(self, *, cursor=None):
            session_state["list_tools_cursors"].append(cursor)
            if cursor is None:
                return SimpleNamespace(
                    tools=[
                        Tool(
                            name="first_page",
                            description="First page",
                            inputSchema={"type": "object", "properties": {}},
                        )
                    ],
                    nextCursor="next",
                )
            return SimpleNamespace(
                tools=[
                    Tool(
                        name="second_page",
                        description="Second page",
                        inputSchema={"type": "object", "properties": {}},
                    )
                ],
                nextCursor=None,
            )

    def fake_streamablehttp_client(endpoint_url, *, headers, timeout):
        return FakeStreamContext()

    monkeypatch.setattr(
        "agent_bridge.capability_hub.sources.mcp.http_client.streamablehttp_client",
        fake_streamablehttp_client,
    )
    monkeypatch.setattr("agent_bridge.capability_hub.sources.mcp.http_client.ClientSession", FakeClientSession)

    tools = asyncio.run(
        McpHttpClient().list_tools(
            "https://example.test/mcp",
            headers={"Authorization": "Bearer token"},
        )
    )

    assert session_state["initialize_calls"] == 1
    assert session_state["list_tools_cursors"] == [None, "next"]
    assert [tool["name"] for tool in tools] == ["first_page", "second_page"]


def test_normalize_call_tool_result_handles_text_content() -> None:
    result = SimpleNamespace(
        isError=False,
        content=[TextContent(type="text", text='{"rows": [{"id": 1}]}')],
        structuredContent=None,
    )

    payload = normalize_call_tool_result(result)

    assert payload == {
        "is_error": False,
        "structured": None,
        "content": [{"type": "text", "text": '{"rows": [{"id": 1}]}'}],
    }
