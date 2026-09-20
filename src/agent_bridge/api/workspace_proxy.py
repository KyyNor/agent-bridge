"""DSH Workspace 反向代理：``/agent-workspace/**`` → 当前用户 runtime。

复用 dashboard 代理的流式转发骨架（HTTP/SSE 逐块转发、断连检测、hop-by-hop
头剥离），并补充 Workspace 必需的语义：

- 目标只能来自当前业务用户已登记的 runtime 状态（``require_runtime_target``），
  不接受 URL 参数指定任意 localhost 端口；
- 未运行时兜底触发 ``ensure_running``（线程化，避免阻塞事件循环）；
- HTTP/WebSocket 双通道；Host/Origin 改写为目标 origin，Location 统一重写回
  ``/agent-workspace`` 前缀，浏览器地址保持 Agent Bridge 域名与端口；
- DSH 前端以 ``<base href="/">`` 用根绝对路径请求资源、插件与 ``/api/**``，
  这些请求虽不在前缀下但属于工作台，按 ``workspace_escape_path`` 归属；
- 根导航由代理在服务端完成 DSH 的 token→Cookie 换取，token 既不出现在浏览器
  地址栏，也不会因 runtime 重启后的旧 Cookie 而卡死在 401；换取请求不带任何
  浏览器 Cookie（旧会话会让真实 DSH 只回 303 而不下发新 Cookie，形成无限
  重定向），其余发往 DSH 的 Cookie 也收敛为 DSH 自己的（``dsh-`` 前缀）。
  DSH 的启动 token 会随 Connection 重载轮换并重印横幅，换取失败时重扫日志
  取最新 token 重试一次；仍失败则不带 token 直接代理原请求（既有会话有效
  则 200，否则如实 401），绝不把带 token 查询的 303 回放给浏览器；
- 代理命中即刷新 runtime ``last_access_at``，供空闲回收使用。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
from contextlib import suppress
from urllib.parse import urlsplit, urlunsplit

import httpx
from starlette.types import ASGIApp, Receive, Scope, Send

from agent_bridge.api.dashboard_proxy import (
    HOP_BY_HOP_HEADERS,
    RESPONSE_HEADERS_TO_DROP,
    _forward_headers,
    _proxy_stream_response,
)
from agent_bridge.core.domain import AgentBridgeError, BackendUnavailable
from agent_bridge.dsh.workspace import WORKSPACE_PROXY_PREFIX

logger = logging.getLogger(__name__)

# ensure_running 最长等待：启动探测窗口（60s）加回收余量，并覆盖首次初始化
# 时先于 web 进程同步执行的插件安装（冷缓存下载较慢，超时后重试可续装）。
ENSURE_TIMEOUT_SECONDS = 240.0
_WS_CLOSE_TIMEOUT_SECONDS = 5.0
_EXCHANGE_TIMEOUT_SECONDS = 30.0

# Agent Bridge 自身占用的根路径前缀。工作台的逃逸路由（见 workspace_escape_path）
# 必须让开这些前缀，避免抢走平台自己的接口与静态资源。
RESERVED_PATH_PREFIXES = (
    "/api/v1",
    "/agent-bridge",
    "/agent-workspace",
    "/static/capabilities",
    "/dashboard",
    "/memory-dashboard",
    "/health",
)


def dsh_session_cookie_name(authority: str) -> str:
    """DSH 浏览器会话 Cookie 名：``dsh-auth-`` + authority 的 SHA-256（base64url）。

    DSH 把会话 Cookie 绑定到请求 authority（Host:port），并用进程内 signing
    secret 签名——因此它跨进程重启后必然失效，只能用于定位自己换取的 Cookie，
    不能当作“已完成鉴权”的依据。
    """
    digest = hashlib.sha256(authority.encode("utf-8")).digest()
    return "dsh-auth-" + base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def match_workspace_path(path: str) -> str | None:
    """匹配 ``/agent-workspace`` 前缀，返回去前缀后的上游路径。"""
    if path == WORKSPACE_PROXY_PREFIX:
        return "/"
    prefix = WORKSPACE_PROXY_PREFIX + "/"
    if path.startswith(prefix):
        suffix = path[len(prefix):]
        return f"/{suffix}"
    return None


def is_reserved_path(path: str) -> bool:
    """路径是否属于 Agent Bridge 自身的接口或静态资源。"""
    return any(
        path == reserved or path.startswith(reserved + "/")
        for reserved in RESERVED_PATH_PREFIXES
    )


def workspace_escape_path(scope: Scope) -> str | None:
    """非前缀路径中实际属于工作台的请求路径；不属于则返回 None。

    DSH 前端期望独占源站根路径，所有资源、插件模块和 ``/api/**`` 都用根绝对
    路径请求，因此需要在前缀之外认领它们：

    - HTTP：这些请求由工作台页面发起，携带 ``Referer: …/agent-workspace/…``；
    - WebSocket：浏览器不发送 Referer（DSH 的 ``/api/remote.mux`` 只有同源
      ``Origin``），而 Agent Bridge 自身没有 WS 端点，因此同源 WS 归工作台。

    其余路径（含 ``/api/v1/**`` 等保留前缀）一律不进代理。
    """
    path = str(scope.get("path", ""))
    if not path.startswith("/") or is_reserved_path(path):
        return None
    if scope.get("type") == "websocket":
        return path if _is_same_origin_websocket(scope) else None
    if scope.get("type") != "http":
        return None
    referer = _header_value(scope, "referer")
    if referer is None:
        return None
    referer_path = urlsplit(referer).path
    if referer_path == WORKSPACE_PROXY_PREFIX or referer_path.startswith(WORKSPACE_PROXY_PREFIX + "/"):
        return path
    return None


def _header_value(scope: Scope, name: str) -> str | None:
    wanted = name.encode("latin-1")
    for raw_name, raw_value in scope.get("headers", []):
        if bytes(raw_name).lower() == wanted:
            return bytes(raw_value).decode("latin-1")
    return None


def _origin_override(scope: Scope, target: str) -> dict[str, str]:
    """把浏览器 Origin 改写为上游 origin（无 Origin 时不注入）。

    DSH 的 ``/api/**`` 浏览器信任栅栏要求 Host 为回环地址且 Origin 与之匹配，
    透传 Agent Bridge 的 Origin 会被拒绝（403）。
    """
    if _header_value(scope, "origin") is None:
        return {}
    parts = urlsplit(target)
    return {"origin": f"{parts.scheme}://{parts.netloc}"}


def _request_overrides(scope: Scope, target: str) -> dict[str, str | None]:
    """通用请求头覆盖：Origin 改写 + Cookie 收敛到 DSH 自己的范围。"""
    return {
        **_origin_override(scope, target),
        "cookie": _dsh_request_cookies(
            _header_value(scope, "cookie"), authority=urlsplit(target).netloc
        ),
    }


def _forward_headers_for_exchange(scope: Scope, target: str) -> dict[str, str]:
    """构造 token→Cookie 交换请求头：不带任何浏览器 Cookie。

    交换必须从干净会话开始：真实 DSH 在命中旧会话 Cookie 时可能只回 303 而
    不重新下发 Set-Cookie，代理因此检测不到新 Cookie、把 303 原样转回浏览器，
    形成根导航的无限重定向。平台会话、统计等无关 Cookie 同样不进上游。
    """
    headers = _forward_headers(scope.get("headers", []), urlsplit(target).netloc)
    headers.pop("cookie", None)
    headers.update(_origin_override(scope, target))
    return headers


def _is_same_origin_websocket(scope: Scope) -> bool:
    """WS 握手是否来自本站页面（Origin 与请求 Host 同 authority）。"""
    origin = _header_value(scope, "origin")
    if origin is None:
        return False
    origin_parts = urlsplit(origin)
    host = _header_value(scope, "host") or str(scope.get("server", ("", 0))[0])
    if not origin_parts.netloc:
        return False
    return origin_parts.netloc == host


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
        rewritten.append((name.encode("latin-1"), value.encode("latin-1")))
    return rewritten


def _session_cookie_pair(headers: httpx.Headers, authority: str) -> str | None:
    """从 DSH 响应中取出 ``dsh-auth-*`` 会话 Cookie 的 ``name=value``。"""
    name = dsh_session_cookie_name(authority)
    for raw in headers.get_list("set-cookie"):
        pair = raw.split(";", 1)[0].strip()
        if pair.partition("=")[0].strip() == name and "=" in pair:
            return pair
    return None


def _session_cookie_headers(headers: httpx.Headers, authority: str) -> list[tuple[bytes, bytes]]:
    """原样转发 DSH 写入的会话 Cookie（保留 Path=/ 等属性）。

    工作台请求可能落在 ``/api/**`` 等前缀之外的路径上，Cookie 必须覆盖整站才能
    被浏览器回传；该 Cookie 为 HttpOnly + SameSite=Strict，只经本代理送往 DSH。
    """
    name = dsh_session_cookie_name(authority)
    forwarded: list[tuple[bytes, bytes]] = []
    for raw in headers.get_list("set-cookie"):
        if raw.split(";", 1)[0].partition("=")[0].strip() == name:
            forwarded.append((b"set-cookie", raw.encode("latin-1")))
    return forwarded


def _merge_session_cookie(cookie_header: str | None, *, authority: str, pair: str) -> str:
    """用刚换取的会话 Cookie 替换同名旧值，保留其他 Cookie。"""
    name = dsh_session_cookie_name(authority)
    kept = [
        segment.strip()
        for segment in (cookie_header or "").split(";")
        if segment.strip() and segment.split("=", 1)[0].strip() != name
    ]
    kept.append(pair)
    return "; ".join(kept)


def _dsh_request_cookies(cookie_header: str | None, *, authority: str) -> str | None:
    """收敛发往 DSH 的 Cookie：只保留 DSH 自己需要的部分。

    浏览器会把 Agent Bridge 域下的全部 Cookie 送来（``agent_bridge_admin``、
    PostHog 等），它们与 DSH 无关，不透传给上游。保留的范围：

    - 当前 authority 的 ``dsh-auth-<hash>`` 会话 Cookie——其他端口的旧会话
      Cookie 名称不同，对本目标永远无效；
    - 其余 ``dsh-`` 前缀的应用 Cookie。

    返回 ``None`` 表示没有可转发内容，调用方应删除 Cookie 头而非发送空值。
    """
    session = dsh_session_cookie_name(authority)
    kept: list[str] = []
    for segment in (cookie_header or "").split(";"):
        segment = segment.strip()
        if not segment:
            continue
        cookie_name = segment.partition("=")[0].strip()
        if not cookie_name.startswith("dsh-"):
            continue
        if cookie_name.startswith("dsh-auth-") and cookie_name != session:
            continue
        kept.append(segment)
    return "; ".join(kept) if kept else None


def _redirect_target(location: str, *, target: str) -> tuple[str, str]:
    """把上游 Location 解析成上游路径与查询串；外部地址退化为根路径。"""
    parts = urlsplit(location)
    if parts.scheme or parts.netloc:
        if parts.scheme not in {"http", "https"} or parts.netloc != urlsplit(target).netloc:
            return "/", ""
    return parts.path or "/", parts.query


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
    """构造上游 WS 握手头：Host/Origin 指向目标，剥离握手与 hop-by-hop 头。

    Cookie 收敛到 DSH 自己的范围（``_dsh_request_cookies``）：会话 Cookie 必须
    透传给 DSH，但平台会话、统计等无关 Cookie 不进上游。
    """
    target_parts = urlsplit(target)
    target_origin = f"{target_parts.scheme}://{target_parts.netloc}"
    forwarded: list[tuple[str, str]] = [("host", target_parts.netloc)]
    for name, value in scope.get("headers", []):
        lower = bytes(name).lower()
        if lower in HOP_BY_HOP_HEADERS or lower in _WS_HANDSHAKE_HEADERS:
            continue
        if lower == b"cookie":
            cookies = _dsh_request_cookies(
                bytes(value).decode("latin-1"), authority=target_parts.netloc
            )
            if cookies is None:
                continue
            forwarded.append(("cookie", cookies))
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
            upstream_path = workspace_escape_path(scope)
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

        auth = self._workspace_auth(user_id, upstream_path, scope)
        if auth is not None:
            auth_path, auth_query = auth
            await self._proxy_root_navigation(
                scope,
                receive,
                send,
                user_id=user_id,
                target=target,
                upstream_path=auth_path,
                auth_query=auth_query,
            )
            return

        await _proxy_stream_response(
            scope,
            receive,
            send,
            upstream_path=upstream_path,
            target=target,
            location_prefix=WORKSPACE_PROXY_PREFIX,
            location_key="",
            error_label="DSH workspace proxy failed",
            response_header_builder=lambda headers: _workspace_response_headers(
                headers, target=target
            ),
            request_header_overrides=_request_overrides(scope, target),
        )

    def _workspace_auth(
        self, user_id: str, upstream_path: str, scope: Scope
    ) -> tuple[str, str] | None:
        """根导航需要补 DSH 启动 token 时返回该鉴权入口。

        只在根导航（DSH 应用入口）补：其他路径必须已有会话 Cookie，浏览器带着
        URL 里的 token 再次访问时也不再覆盖查询串。
        """
        if upstream_path != "/" or str(scope.get("method", "GET")) not in {"GET", "HEAD"}:
            return None
        if "token=" in scope.get("query_string", b"").decode("latin-1"):
            return None
        return self.service.dsh.workspace_auth(user_id)

    async def _perform_exchange(
        self,
        scope: Scope,
        *,
        user_id: str,
        target: str,
        upstream_path: str,
        auth_query: str,
    ) -> httpx.Response | None:
        """发起一次 token→Cookie 交换请求；网络异常时返回 None。"""
        headers = _forward_headers_for_exchange(scope, target)
        exchange_url = urlunsplit(
            (urlsplit(target).scheme, urlsplit(target).netloc, upstream_path, auth_query, "")
        )
        timeout = httpx.Timeout(connect=10.0, read=_EXCHANGE_TIMEOUT_SECONDS, write=10.0, pool=10.0)
        try:
            async with httpx.AsyncClient(follow_redirects=False, timeout=timeout) as client:
                return await client.request(
                    str(scope.get("method", "GET")), exchange_url, headers=headers
                )
        except httpx.HTTPError as exc:
            logger.warning(
                "DSH Workspace 首访鉴权请求失败 user=%s url=%s 原因=%s", user_id, exchange_url, exc
            )
            return None

    async def _proxy_root_navigation(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        user_id: str,
        target: str,
        upstream_path: str,
        auth_query: str,
    ) -> None:
        """服务端完成 DSH 首次鉴权：带 token 换取会话 Cookie 后转发最终页面。

        每次根导航都先尝试 ``GET /?token=…`` 换取（换取请求不带任何浏览器
        Cookie，见 ``_forward_headers_for_exchange``），成功后在服务端吞掉
        DSH 的 303、携带新鲜 Cookie follow 最终页面，浏览器直接拿到 200。

        DSH 的启动 token 会随 Connection 重载静默轮换并重印横幅，状态里捕获
        的入口可能已过期（交换 401）；因此换取失败时先重扫日志取最新 token
        重试一次。仍换取不到（token 与浏览器会话双双失效）时改按原请求直接
        代理、不带 token 查询：浏览器既有会话有效则照常 200，否则如实收到
        DSH 的 401。绝不把带 token 查询的响应回放给浏览器——DSH 对“已认证 +
        token 查询”只回去掉查询串的 303 且不下发 Set-Cookie，回放会让浏览器
        在 ``/agent-workspace/`` 上无限重定向。
        """
        target_parts = urlsplit(target)
        authority = target_parts.netloc

        exchange = await self._perform_exchange(
            scope, user_id=user_id, target=target, upstream_path=upstream_path, auth_query=auth_query
        )
        if exchange is None:
            await _send_plain(send, 502, b"DSH workspace proxy failed: exchange request error")
            return

        pair = _session_cookie_pair(exchange.headers, authority)
        if pair is None:
            try:
                refreshed = await asyncio.to_thread(
                    self.service.dsh.refresh_workspace_auth, user_id
                )
            except Exception as exc:
                logger.warning(
                    "DSH Workspace 重扫鉴权横幅失败 user=%s 原因=%s（沿用既有入口继续）",
                    user_id,
                    exc,
                )
                refreshed = None
            if refreshed is not None and refreshed[1] != auth_query:
                logger.info(
                    "DSH Workspace 启动 token 已轮换 user=%s，使用最新鉴权入口重试换取", user_id
                )
                retry = await self._perform_exchange(
                    scope,
                    user_id=user_id,
                    target=target,
                    upstream_path=refreshed[0],
                    auth_query=refreshed[1],
                )
                if retry is not None:
                    exchange = retry
                    pair = _session_cookie_pair(exchange.headers, authority)

        if pair is None:
            logger.warning(
                "DSH Workspace 未换取到会话 Cookie user=%s status=%s（改按原请求直连代理）",
                user_id,
                exchange.status_code,
            )
            await _proxy_stream_response(
                scope,
                receive,
                send,
                upstream_path=upstream_path,
                target=target,
                location_prefix=WORKSPACE_PROXY_PREFIX,
                location_key="",
                error_label="DSH workspace proxy failed",
                response_header_builder=lambda headers: _workspace_response_headers(
                    headers, target=target
                ),
                request_header_overrides=_request_overrides(scope, target),
            )
            return

        location = exchange.headers.get("location")
        if location:
            follow_path, follow_query = _redirect_target(location, target=target)
        else:
            follow_path, follow_query = upstream_path, ""
        logger.info(
            "DSH Workspace 首访鉴权完成 user=%s status=%s follow=%s",
            user_id,
            exchange.status_code,
            follow_path,
        )
        await _proxy_stream_response(
            scope,
            receive,
            send,
            upstream_path=follow_path,
            target=target,
            location_prefix=WORKSPACE_PROXY_PREFIX,
            location_key="",
            error_label="DSH workspace proxy failed",
            response_header_builder=lambda headers: _workspace_response_headers(
                headers, target=target
            ),
            query_override=follow_query,
            request_header_overrides={
                "cookie": _merge_session_cookie(
                    _dsh_request_cookies(_header_value(scope, "cookie"), authority=authority),
                    authority=authority,
                    pair=pair,
                ),
                **_origin_override(scope, target),
            },
            extra_response_headers=_session_cookie_headers(exchange.headers, authority),
        )

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


def _target_origin(target: str) -> str:
    parts = urlsplit(target)
    return f"{parts.scheme}://{parts.netloc}"


async def _send_plain(send: Send, status: int, body: bytes, *, headers: list[tuple[bytes, bytes]] | None = None) -> None:
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": headers or [(b"content-type", b"text/plain; charset=utf-8")],
    })
    await send({"type": "http.response.body", "body": body})
