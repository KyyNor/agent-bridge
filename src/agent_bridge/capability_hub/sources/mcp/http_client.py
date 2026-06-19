from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import timedelta
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import Tool


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    return value


def normalize_tool(tool: Tool) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description or "",
        "input_schema": tool.inputSchema or {"type": "object", "properties": {}},
        "annotations": _plain(getattr(tool, "annotations", None)),
        "output_schema": _plain(getattr(tool, "outputSchema", None)),
    }


def normalize_call_tool_result(result: Any) -> dict[str, Any]:
    return {
        "is_error": bool(getattr(result, "isError", False)),
        "structured": _plain(getattr(result, "structuredContent", None)),
        "content": _plain(getattr(result, "content", [])),
    }


class McpHttpClient:
    async def list_tools(
        self,
        endpoint_url: str,
        headers: dict[str, str],
        timeout: float = 30.0,
    ) -> list[dict[str, Any]]:
        async with streamablehttp_client(endpoint_url, headers=headers, timeout=timeout) as (
            read_stream,
            write_stream,
            _get_session_id,
        ):
            async with ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=timeout),
            ) as session:
                await session.initialize()
                tools: list[dict[str, Any]] = []
                cursor: str | None = None
                while True:
                    result = await session.list_tools(cursor=cursor)
                    tools.extend(normalize_tool(tool) for tool in result.tools)
                    cursor = getattr(result, "nextCursor", None)
                    if cursor is None:
                        return tools

    async def call_tool(
        self,
        endpoint_url: str,
        headers: dict[str, str],
        tool_name: str,
        arguments: dict[str, Any],
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        async with streamablehttp_client(endpoint_url, headers=headers, timeout=timeout) as (
            read_stream,
            write_stream,
            _get_session_id,
        ):
            async with ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=timeout),
            ) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                return normalize_call_tool_result(result)
