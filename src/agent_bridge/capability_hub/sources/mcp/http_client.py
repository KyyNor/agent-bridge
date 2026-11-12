from __future__ import annotations

import logging
from dataclasses import asdict, is_dataclass
from datetime import timedelta
from typing import Any

import httpx

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.types import Tool

from agent_bridge.core.defaults import DEFAULT_MCP_TIMEOUT_SECONDS
from agent_bridge.core.domain import ValidationError
from agent_bridge.capability_hub.sources.mcp.headers import validate_header
from agent_bridge.capability_hub.sources.mcp.sync_errors import McpToolSyncError

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
        try:
            for name, value in headers.items():
                validate_header(name, value)
        except ValidationError as exc:
            raise McpToolSyncError(exc.message, "invalid_headers") from exc

        upstream_failure: McpToolSyncError | None = None

        async def capture_response(response: httpx.Response) -> None:
            nonlocal upstream_failure
            # GET/SSE 和 DELETE 可能在关闭期间失败，工具请求的 HTTP 状态优先。
            if response.request.method == "POST" and response.is_error:
                upstream_failure = McpToolSyncError.from_response(response)
                raise upstream_failure

        def client_factory(**kwargs: Any) -> httpx.AsyncClient:
            client = create_mcp_http_client(**kwargs)
            client.event_hooks["response"].append(capture_response)
            return client

        try:
            async with streamablehttp_client(
                endpoint_url, headers=headers, timeout=timeout, httpx_client_factory=client_factory
            ) as (
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
        except Exception as exc:
            # MCP SDK 的写协程可能吞掉原异常，调用方只收到连接关闭。
            if upstream_failure is not None:
                raise McpToolSyncError(
                    upstream_failure.message, upstream_failure.code,
                    upstream_status=upstream_failure.upstream_status,
                ) from exc
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
