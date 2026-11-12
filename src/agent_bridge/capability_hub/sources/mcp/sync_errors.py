"""MCP 同步失败的安全诊断。"""

from collections.abc import Iterator

import httpx
from mcp.shared.exceptions import McpError

from agent_bridge.core.domain import AgentBridgeError


def exception_leaves(exc: BaseException) -> Iterator[BaseException]:
    """展开 MCP SDK 的任务组。"""
    seen: set[int] = set()
    stack = [exc]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, BaseExceptionGroup):
            stack.extend(reversed(current.exceptions))
        else:
            yield current


class McpToolSyncError(AgentBridgeError):
    """只携带由平台生成的诊断，避免上游消息回显凭据。"""

    def __init__(self, message: str, code: str, *, upstream_status: int | None = None) -> None:
        self.status_code = 504 if code == "timeout" else 502
        self.code = code
        self.upstream_status = upstream_status
        super().__init__(message)

    @classmethod
    def from_response(cls, response: httpx.Response) -> "McpToolSyncError":
        status = response.status_code
        if status in (401, 403) or "www-authenticate" in response.headers:
            message = f"MCP 服务鉴权失败（上游 HTTP {status}），请检查请求 Header 和访问权限"
            return cls(message, "upstream_auth", upstream_status=status)
        if status == 400:
            message = "MCP 服务拒绝请求（上游 HTTP 400），请检查 MCP 端点、鉴权 Header 和服务配置"
        else:
            message = f"MCP 服务返回 HTTP {status}，请检查服务状态和端点配置"
        return cls(message, "upstream_http", upstream_status=status)

    @classmethod
    def from_exception(cls, exc: Exception) -> "McpToolSyncError":
        leaves = list(exception_leaves(exc))
        for leaf in leaves:
            if isinstance(leaf, cls):
                return leaf
            if isinstance(leaf, httpx.HTTPStatusError):
                return cls.from_response(leaf.response)
        for leaf in leaves:
            if isinstance(leaf, (TimeoutError, httpx.TimeoutException)):
                return cls("连接 MCP 服务或等待响应超时", "timeout")
            if isinstance(leaf, McpError):
                return cls(f"MCP 服务返回协议错误（{leaf.error.code}）", "upstream_protocol")
            if isinstance(leaf, httpx.RequestError):
                return cls("MCP 请求失败，请检查端点地址、网络和请求 Header", "upstream_transport")
        return cls("无法读取 MCP 工具列表，请检查服务状态和配置", "upstream_failure")
