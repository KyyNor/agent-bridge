from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from agent_bridge.capabilities.mcp_http_client import normalize_call_tool_result


class CodeGraphMcpClient:
    def __init__(self, cli_path: str = "codegraph") -> None:
        self.cli_path = cli_path

    async def call_tool(
        self,
        project_dir: Path,
        tool_name: str,
        arguments: dict[str, Any],
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        params = StdioServerParameters(
            command=self.cli_path,
            args=["serve", "--mcp", "--path", str(project_dir), "--no-watch"],
            cwd=project_dir,
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
