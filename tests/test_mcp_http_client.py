from __future__ import annotations

from types import SimpleNamespace

from mcp.types import TextContent, Tool

from wiki_manager.mcp_http_client import normalize_call_tool_result, normalize_tool


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
    }


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
