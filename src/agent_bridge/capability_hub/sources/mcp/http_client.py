from __future__ import annotations

import logging
from dataclasses import asdict, is_dataclass
from datetime import timedelta
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import Tool

from agent_bridge.core.defaults import DEFAULT_MCP_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)


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
        timeout: float = DEFAULT_MCP_TIMEOUT_SECONDS,
    ) -> list[dict[str, Any]]:
        logger.info("MCP list_tools 开始 endpoint=%s timeout=%ss", endpoint_url, timeout)
        try:
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
                            logger.info("MCP list_tools 完成 endpoint=%s 工具数=%d", endpoint_url, len(tools))
                            return tools
        except TimeoutError:
            logger.warning("MCP list_tools 超时 endpoint=%s timeout=%ss", endpoint_url, timeout)
            raise
        except Exception as exc:
            logger.error(
                "MCP list_tools 失败 endpoint=%s 原因=%s",
                endpoint_url,
                exc,
                exc_info=True,
            )
            raise

    async def call_tool(
        self,
        endpoint_url: str,
        headers: dict[str, str],
        tool_name: str,
        arguments: dict[str, Any],
        timeout: float = DEFAULT_MCP_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        logger.debug("MCP call_tool 开始 endpoint=%s tool=%s", endpoint_url, tool_name)
        try:
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
                    normalized = normalize_call_tool_result(result)
                    logger.debug(
                        "MCP call_tool 完成 endpoint=%s tool=%s is_error=%s",
                        endpoint_url,
                        tool_name,
                        normalized.get("is_error"),
                    )
                    return normalized
        except TimeoutError:
            logger.warning("MCP call_tool 超时 endpoint=%s tool=%s timeout=%ss", endpoint_url, tool_name, timeout)
            raise
        except Exception as exc:
            logger.error(
                "MCP call_tool 失败 endpoint=%s tool=%s 原因=%s",
                endpoint_url,
                tool_name,
                exc,
                exc_info=True,
            )
            raise
