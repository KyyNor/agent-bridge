"""DSH Workspace 反向代理：``/agent-workspace/**`` → 当前用户 runtime。

复用 dashboard 代理的流式转发骨架（HTTP/SSE 逐块转发、断连检测、hop-by-hop
头剥离），并补充 Workspace 必需的语义：

- 目标只能来自当前业务用户已登记的 runtime 状态（``require_runtime_target``），
  不接受 URL 参数指定任意 localhost 端口；
- 未运行时兜底触发 ``ensure_running``（线程化，避免阻塞事件循环）；
- HTTP/WebSocket 双通道；Host/Origin 改写为目标 origin，Location 与
  Set-Cookie 的 Path 统一重写回 ``/agent-workspace`` 前缀，浏览器地址保持
  Agent Bridge 域名；
- 代理命中即刷新 runtime ``last_access_at``，供空闲回收使用。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
from contextlib import suppress
from urllib.parse import urlsplit, urlunsplit

import httpx
from starlette.types import ASGIApp, Receive, Scope, Send

from agent_bridge.api.dashboard_proxy import (
    HOP_BY_HOP_HEADERS,
    RESPONSE_HEADERS_TO_DROP,
    _proxy_stream_response,
)
from agent_bridge.core.domain import AgentBridgeError, BackendUnavailable
from agent_bridge.dsh.workspace import WORKSPACE_PROXY_PREFIX

logger = logging.getLogger(__name__)

# ensure_running 最长可等待启动探测窗口（60s）加回收余量。
ENSURE_TIMEOUT_SECONDS = 90.0
_WS_CLOSE_TIMEOUT_SECONDS = 5.0

_SET_COOKIE_PATH_RE = re.compile(r"(?i)(^|;\s*)(path=)([^;]*)")


def _dsh_session_cookie_name(authority: str) -> str:
    """DSH 浏览器会话 Cookie 名：``dsh-auth-`` + authority 的 SHA-256（base64url）。

    DSH 把会话 Cookie 绑定到请求 authority（Host:port），同一 authority 下的
    有效 Cookie 表示已完成 token→Cookie 换取。代理据此判断是否还需要注入
    首访 token，避免在已鉴权时重复触发 DSH 的 303 造成重定向循环。
    """
    digest = hashlib.sha256(authority.encode("utf-8")).digest()
    return "dsh-auth-" + base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _has_dsh_session_cookie(scope: Scope, authorization_authority: str) -> bool:
    expected = _dsh_session_cookie_name(authorization_authority)
    for name, value in scope.get("headers", []):
        if bytes(name).lower() != b"cookie":
            continue
        for segment in bytes(value).decode("latin-1").split(";"):
            if "=" in segment and segment.split("=", 1)[0].strip() == expected:
                return True
    return False


def match_workspace_path(path: str) -> str | None:
    """匹配 ``/agent-workspace`` 前缀，返回去前缀后的上游路径。"""
    if path == WORKSPACE_PROXY_PREFIX:
        return "/"
    prefix = WORKSPACE_PROXY_PREFIX + "/"
    if path.startswith(prefix):
        suffix = path[len(prefix):]
        return f"/{suffix}"
    return None


def rewrite_workspace_location(location: str, *, target: str) -> str:
    """把上游 Location 重写回 Workspace 前缀，浏览器不感知 DSH 端口。"""
    parts = urlsplit(location)
    target_parts = urlsplit(target)
    if parts.scheme or parts.netloc:
        if parts.scheme not in {"http", "https"} or parts.netloc != target_parts.netloc:
            return location
        path, query, fragment = parts.path or "/", parts.query, parts.fragment
    else:
        path, query, fragment = parts.path or "/", parts.query, parts.fragment
    if path.startswith(f"{WORKSPACE_PROXY_PREFIX}/") or path == WORKSPACE_PROXY_PREFIX:
        rewritten_path = path
    elif path.startswith("/"):
        rewritten_path = f"{WORKSPACE_PROXY_PREFIX}{path}"
    else:
        rewritten_path = f"{WORKSPACE_PROXY_PREFIX}/{path}"
    return urlunsplit(("", "", rewritten_path, query, fragment))


def rewrite_workspace_set_cookie(value: str) -> str:
    """把 Set-Cookie 的 Path 归一到 Workspace 前缀下，避免 Cookie 泄出前缀。"""

    def _patch(match: re.Match[str]) -> str:
        lead, attr, path = match.group(1), match.group(2), match.group(3)
        if path.startswith(f"{WORKSPACE_PROXY_PREFIX}/") or path == WORKSPACE_PROXY_PREFIX:
            return match.group(0)
        joined = path if path.startswith("/") else f"/{path}"
        return f"{lead}{attr}{WORKSPACE_PROXY_PREFIX}{joined}"

    return _SET_COOKIE_PATH_RE.sub(_patch, value)


def _workspace_response_headers(
    headers: httpx.Headers, *, target: str
) -> list[tuple[bytes, bytes]]:
    rewritten: list[tuple[bytes, bytes]] = []
    for name, value in headers.multi_items():
        lower = name.lower().encode("latin-1")
        if lower in RESPONSE_HEADERS_TO_DROP:
            continue
        if name.lower() == "location":
            value = rewrite_workspace_location(value, target=target)
        elif name.lower() == "set-cookie":
            value = rewrite_workspace_set_cookie(value)
        rewritten.append((name.encode("latin-1"), value.encode("latin-1")))
    return rewritten


# 上游握手由 websockets client 自行生成；下游的握手/协商头必须剥离，
# 避免重复的 Sec-WebSocket-Key 等导致上游 400。Cookie 需要透传给 DSH。
_WS_HANDSHAKE_HEADERS = {
    b"host",
    b"origin",
    b"sec-websocket-key",
    b"sec-websocket-version",
    b"sec-websocket-protocol",
    b"sec-websocket-extensions",
    b"sec-websocket-accept",
}


def _forward_ws_headers(scope: Scope, target: str) -> list[tuple[str, str]]:
    """构造上游 WS 握手头：Host/Origin 指向目标，剥离握手与 hop-by-hop 头。"""
    target_parts = urlsplit(target)
    target_origin = f"{target_parts.scheme}://{target_parts.netloc}"
    forwarded: list[tuple[str, str]] = [("host", target_parts.netloc)]
    for name, value in scope.get("headers", []):
        lower = bytes(name).lower()
        if lower in HOP_BY_HOP_HEADERS or lower in _WS_HANDSHAKE_HEADERS:
            continue
        forwarded.append((bytes(name).decode("latin-1"), bytes(value).decode("latin-1")))
    forwarded.append(("origin", target_origin))
    return forwarded


class AgentWorkspaceProxyMiddleware:
    def __init__(self, app: ASGIApp, *, service, identity_resolver) -> None:
        self.app = app
        self.service = service
        self.identity_resolver = identity_resolver

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = str(scope.get("path", ""))
        upstream_path = match_workspace_path(path)
        if upstream_path is None:
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            await self._proxy_websocket(scope, receive, send, upstream_path)
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        from fastapi import Request

        try:
            user_id = self.identity_resolver.resolve(Request(scope)).user_id
            target = await self._resolve_target(user_id)
        except AgentBridgeError as exc:
            await _send_plain(send, exc.status_code, exc.message.encode("utf-8"))
            return
        if target is None:
            await _send_plain(send, 404, "当前用户没有运行中的 DSH 工作台".encode("utf-8"))
            return

        # DSH 首访必须携带启动 token：服务端用它完成 token→Cookie 换取，
        # 浏览器地址栏与后续请求都不出现该 token。
        auth_path, auth_query = self._workspace_auth(user_id, target, upstream_path, scope)
        await _proxy_stream_response(
            scope,
            receive,
            send,
            upstream_path=auth_path,
            target=target,
            location_prefix=WORKSPACE_PROXY_PREFIX,
            location_key="",
            error_label="DSH workspace proxy failed",
            response_header_builder=lambda headers: _workspace_response_headers(
                headers, target=target
            ),
            query_override=auth_query,
        )

    def _workspace_auth(
        self, user_id: str, target: str, upstream_path: str, scope: Scope
    ) -> tuple[str, str | None]:
        """仅在工作台根导航、且尚未持有 DSH 会话 Cookie 时补首访 token。"""
        if upstream_path != "/":
            return upstream_path, None
        query = scope.get("query_string", b"").decode("latin-1")
        if "token=" in query:
            return upstream_path, None
        authority = urlsplit(target).netloc
        if _has_dsh_session_cookie(scope, authority):
            return upstream_path, None
        entry = self.service.dsh.workspace_auth(user_id)
        if entry is None:
            return upstream_path, None
        auth_path, auth_query = entry
        return auth_path or "/", auth_query

    async def _resolve_target(self, user_id: str) -> str | None:
        target = self.service.dsh.require_runtime_target(user_id)
        if target is not None:
            return target
        logger.info("DSH Workspace proxy triggered on-demand startup user=%s", user_id)
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self.service.dsh.ensure_running, user_id),
                timeout=ENSURE_TIMEOUT_SECONDS,
            )
        except AgentBridgeError:
            # AccessDenied/ValidationError etc. maintain their original status codes and are
            # returned directly to the caller (e.g., users without a group receive 403)
            raise
        except TimeoutError:
            logger.warning("DSH Workspace on-demand startup timed out user=%s timeout=%.0fs", user_id, ENSURE_TIMEOUT_SECONDS)
            raise BackendUnavailable("DSH workspace startup timed out, please try again later") from None
        return self.service.dsh.require_runtime_target(user_id)

    async def _proxy_websocket(
        self, scope: Scope, receive: Receive, send: Send, upstream_path: str
    ) -> None:
        from websockets.asyncio.client import connect
        from websockets.exceptions import WebSocketException

        connect_message = await receive()
        if connect_message.get("type") != "websocket.connect":
            return
        # Request 会断言 http scope；HTTPConnection 对 websocket scope 同样
        # 提供 headers/cookies，可直接用于身份解析。
        from starlette.requests import HTTPConnection

        try:
            user_id = self.identity_resolver.resolve(HTTPConnection(scope)).user_id
            target = await self._resolve_target(user_id)
        except AgentBridgeError as exc:
            await send({"type": "websocket.close", "code": 1008, "reason": exc.message})
            return
        if target is None:
            await send({"type": "websocket.close", "code": 1011, "reason": "DSH 工作台未运行"})
            return

        target_parts = urlsplit(target)
        scheme = "wss" if target_parts.scheme == "https" else "ws"
        query = scope.get("query_string", b"").decode("latin-1")
        upstream_url = urlunsplit((scheme, target_parts.netloc, upstream_path, query, ""))
        subprotocols = [str(item) for item in scope.get("subprotocols", [])]
        headers = _forward_ws_headers(scope, target)

        try:
            async with connect(
                upstream_url,
                additional_headers=headers,
                subprotocols=subprotocols or None,
                open_timeout=10.0,
                close_timeout=_WS_CLOSE_TIMEOUT_SECONDS,
            ) as upstream:
                negotiated = getattr(upstream, "subprotocol", None)
                await send({
                    "type": "websocket.accept",
                    "subprotocol": negotiated,
                    "headers": [],
                })
                await self._pump_websocket(upstream, receive, send)
        except (WebSocketException, OSError, asyncio.TimeoutError) as exc:
            logger.warning(
                "DSH Workspace WebSocket 上游连接失败 user=%s url=%s 原因=%s",
                user_id,
                upstream_path,
                exc,
            )
            with suppress(Exception):
                await send({"type": "websocket.close", "code": 1011, "reason": "无法连接 DSH 工作台"})

    async def _pump_websocket(self, upstream, receive: Receive, send: Send) -> None:
        """双向转发：任一方向结束即收尾，双向异常都关闭对端。"""

        async def upstream_to_client() -> None:
            async for message in upstream:
                if isinstance(message, str):
                    await send({"type": "websocket.send", "text": message})
                else:
                    await send({"type": "websocket.send", "bytes": message})

        downstream_code: dict[str, int] = {"code": 1000}

        async def client_to_upstream() -> None:
            while True:
                message = await receive()
                if message["type"] == "websocket.receive":
                    data = message.get("bytes")
                    if data is None:
                        await upstream.send(str(message.get("text") or ""))
                    else:
                        await upstream.send(data)
                elif message["type"] == "websocket.disconnect":
                    downstream_code["code"] = int(message.get("code") or 1000)
                    return

        tasks = [
            asyncio.create_task(upstream_to_client(), name="dsh-ws-upstream-to-client"),
            asyncio.create_task(client_to_upstream(), name="dsh-ws-client-to-upstream"),
        ]
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                task.result()
        except Exception:
            logger.info("DSH Workspace WebSocket 连接结束", exc_info=True)
        finally:
            with suppress(Exception):
                await send({
                    "type": "websocket.close",
                    "code": downstream_code["code"],
                })


async def _send_plain(send: Send, status: int, body: bytes, *, headers: list[tuple[bytes, bytes]] | None = None) -> None:
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": headers or [(b"content-type", b"text/plain; charset=utf-8")],
    })
    await send({"type": "http.response.body", "body": body})
