from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from agent_bridge.codegraph.mcp_client import CodeGraphMcpClient


class FakeToolResult:
    isError = False
    structuredContent = {"answer": "ok"}
    content = [{"type": "text", "text": "ok"}]


class FakeStdioContext:
    def __init__(self, recorder: dict[str, Any]) -> None:
        self.recorder = recorder

    async def __aenter__(self) -> tuple[str, str]:
        return ("read", "write")

    async def __aexit__(self, *args: Any) -> None:
        self.recorder["stdio_closed"] = True


class FakeSession:
    def __init__(self, read_stream: str, write_stream: str, **kwargs: Any) -> None:
        self.read_stream = read_stream
        self.write_stream = write_stream
        self.kwargs = kwargs

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    async def initialize(self) -> None:
        pass

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> FakeToolResult:
        assert tool_name == "codegraph_explore"
        assert arguments == {"query": "routing flow"}
        return FakeToolResult()


def test_codegraph_mcp_client_calls_codegraph_serve_in_repo_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    recorder: dict[str, Any] = {}

    def fake_stdio_client(params: Any) -> FakeStdioContext:
        recorder["params"] = params
        return FakeStdioContext(recorder)

    monkeypatch.setattr("agent_bridge.codegraph.mcp_client.stdio_client", fake_stdio_client)
    monkeypatch.setattr("agent_bridge.codegraph.mcp_client.ClientSession", FakeSession)

    result = asyncio.run(
        CodeGraphMcpClient().call_tool(
            tmp_path,
            "codegraph_explore",
            {"query": "routing flow"},
        )
    )

    params = recorder["params"]
    assert params.command == "codegraph"
    assert params.args == ["serve", "--mcp", "--path", str(tmp_path), "--no-watch"]
    assert params.cwd == tmp_path
    assert recorder["stdio_closed"] is True
    assert result["is_error"] is False
    assert result["structured"] == {"answer": "ok"}
