"""CodeGraph 正式后端抽象。

CLI 命令与 MCP stdio 是同一个 CodeGraph 引擎的两种调用通道，统一封装在
一个 adapter 中。应用层不再感知具体传输方式，也不提供语义不等价的隐式后端。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from agent_bridge.knowledge_management.code_knowledge.client import CodeGraphClient
from agent_bridge.knowledge_management.code_knowledge.mcp_client import CodeGraphMcpClient


class CodeGraphBackend(Protocol):
    """CodeGraph 索引与查询契约。"""

    def is_available(self) -> bool: ...
    def build_index(self, project_dir: Path) -> int: ...
    def status(self, project_dir: Path) -> dict[str, Any]: ...
    def query(self, project_dir: Path, term: str, *, limit: int = 20) -> list[dict[str, Any]]: ...
    def files(self, project_dir: Path) -> list[dict[str, Any]]: ...
    def callers(self, project_dir: Path, symbol: str) -> list[dict[str, Any]]: ...
    def callees(self, project_dir: Path, symbol: str) -> list[dict[str, Any]]: ...
    def impact(self, project_dir: Path, symbol: str) -> list[dict[str, Any]]: ...
    async def explore(self, project_dir: Path, query: str, *, timeout: float) -> dict[str, Any]: ...
    def terminate_active_processes(self) -> None: ...


class CliCodeGraphBackend:
    """使用 CodeGraph CLI 建图和查询，并使用其 MCP 模式执行 Explore。"""

    def __init__(
        self,
        client: CodeGraphClient | None = None,
        mcp_client: CodeGraphMcpClient | None = None,
    ) -> None:
        self.client = client or CodeGraphClient()
        self.mcp_client = mcp_client or CodeGraphMcpClient(cli_path=self.client.cli_path)

    def is_available(self) -> bool:
        return self.client.is_available()

    def build_index(self, project_dir: Path) -> int:
        self.client.init(project_dir)
        self.client.index(project_dir)
        return len(self.client.files(project_dir))

    def status(self, project_dir: Path) -> dict[str, Any]:
        return self.client.status(project_dir)

    def query(self, project_dir: Path, term: str, *, limit: int = 20) -> list[dict[str, Any]]:
        return self.client.query(project_dir, term, limit=limit)

    def files(self, project_dir: Path) -> list[dict[str, Any]]:
        return self.client.files(project_dir)

    def callers(self, project_dir: Path, symbol: str) -> list[dict[str, Any]]:
        return self.client.callers(project_dir, symbol)

    def callees(self, project_dir: Path, symbol: str) -> list[dict[str, Any]]:
        return self.client.callees(project_dir, symbol)

    def impact(self, project_dir: Path, symbol: str) -> list[dict[str, Any]]:
        return self.client.impact(project_dir, symbol)

    async def explore(self, project_dir: Path, query: str, *, timeout: float) -> dict[str, Any]:
        return await self.mcp_client.call_tool(
            project_dir,
            "codegraph_explore",
            {"query": query, "projectPath": str(project_dir)},
            timeout=timeout,
        )

    def terminate_active_processes(self) -> None:
        self.client.terminate_active_processes()
