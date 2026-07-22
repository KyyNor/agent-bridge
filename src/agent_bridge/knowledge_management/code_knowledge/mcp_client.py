"""codegraph CLI 的 MCP(stdio) 直连客户端。

每次 ``call_tool`` 临时拉起一个 ``codegraph serve --mcp`` 子进程并经 stdio
与它建立 MCP 会话；这是正式 CodeGraph 后端的 Explore 调用通道。
"""
from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from agent_bridge.capability_hub.sources.mcp.http_client import normalize_call_tool_result
from agent_bridge.core.defaults import DEFAULT_MCP_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)


class CodeGraphMcpClient:
    def __init__(self, cli_path: str = "codegraph") -> None:
        self.cli_path = cli_path

    async def call_tool(
        self,
        project_dir: Path,
        tool_name: str,
        arguments: dict[str, Any],
        timeout: float = DEFAULT_MCP_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """拉起 codegraph MCP 服务并调用指定工具，返回归一化后的结果字典。"""
        params = StdioServerParameters(
            command=self.cli_path,
            args=["serve", "--mcp", "--path", str(project_dir), "--no-watch"],
            cwd=project_dir,
        )
        logger.debug(
            "MCP 直连调用 tool=%s 项目=%s 超时=%ss",
            tool_name, project_dir.name, timeout,
        )
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=timeout),
            ) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                return normalize_call_tool_result(result)
