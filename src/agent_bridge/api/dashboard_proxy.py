from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from typing import Any
from urllib.parse import parse_qs, urlsplit, urlunsplit

import httpx
from starlette.types import ASGIApp, Receive, Scope, Send

from agent_bridge.knowledge_management.code_knowledge.dashboard_urls import external_dashboard_url


HOP_BY_HOP_HEADERS = {
    b"connection",
    b"keep-alive",
    b"proxy-authenticate",
    b"proxy-authorization",
    b"te",
    b"trailer",
    b"transfer-encoding",
    b"upgrade",
}
RESPONSE_HEADERS_TO_DROP = HOP_BY_HOP_HEADERS | {b"content-length", b"content-encoding"}
DASHBOARD_ROOT_ENDPOINTS = {
    "/knowledge-graph.json",
    "/domain-graph.json",
    "/diff-overlay.json",
    "/meta.json",
    "/config.json",
    "/file-content.json",
}
MEMORY_DASHBOARD_ROOT_ASSET_SUFFIXES = {
    ".css",
    ".eot",
    ".gif",
    ".html",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".json",
    ".png",
    ".svg",
    ".ttf",
    ".webp",
    ".woff",
    ".woff2",
}


class DashboardProxyMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        target_resolver: Callable[[str], str | None],
        token_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        self.app = app
        self.target_resolver = target_resolver
        self.token_resolver = token_resolver

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path", ""))
        repo_key, suffix = _match_dashboard_path(path)
        upstream_path = _upstream_path(repo_key, suffix) if repo_key else ""
        if repo_key is None and path in DASHBOARD_ROOT_ENDPOINTS:
            repo_key = _repo_key_from_referer(scope.get("headers", []))
            if repo_key is None and self.token_resolver is not None:
                repo_key = _repo_key_from_token(scope.get("query_string", b""), self.token_resolver)
            upstream_path = path
        if repo_key is None:
            await self.app(scope, receive, send)
            return

        target = self.target_resolver(repo_key)
        if target is None:
            await _send_plain(send, 404, b"dashboard is not running")
            return

        await self._proxy_http(scope, receive, send, repo_key, upstream_path, target)

    async def _proxy_http(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        repo_key: str,
        upstream_path: str,
        target: str,
    ) -> None:
        await _proxy_stream_response(
            scope,
            receive,
            send,
            upstream_path=upstream_path,
            target=target,
            location_prefix="/dashboard",
            location_key=repo_key,
            error_label="dashboard proxy failed",
        )


class MemoryDashboardProxyMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        target_resolver: Callable[[str], str | None],
    ) -> None:
        self.app = app
        self.target_resolver = target_resolver

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path", ""))
        block_key, suffix = _match_memory_dashboard_path(path)
        upstream_path = suffix if block_key else ""
        if block_key is None and (
            (path.startswith("/api/") and not path.startswith("/api/v1/"))
            or path == "/stream"
            or _is_memory_dashboard_root_asset(path)
        ):
            block_key = _memory_key_from_referer(scope.get("headers", []))
            upstream_path = path
        if block_key is None:
            await self.app(scope, receive, send)
            return

        target = self.target_resolver(block_key)
        if target is None:
            await _send_plain(send, 404, b"memory dashboard is not running")
            return

        await self._proxy_http(scope, receive, send, block_key, upstream_path, target)

    async def _proxy_http(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        block_key: str,
        upstream_path: str,
        target: str,
    ) -> None:
        await _proxy_stream_response(
            scope,
            receive,
            send,
            upstream_path=upstream_path,
            target=target,
            location_prefix="/memory-dashboard",
            location_key=block_key,
            error_label="memory dashboard proxy failed",
        )


def _match_dashboard_path(path: str) -> tuple[str | None, str]:
    prefix = "/dashboard/"
    if not path.startswith(prefix):
        return None, ""
    rest = path[len(prefix):]
    if not rest:
        return None, ""
    repo_key, _, suffix = rest.partition("/")
    if not repo_key:
        return None, ""
    return repo_key, f"/{suffix}" if suffix else "/"


def _match_memory_dashboard_path(path: str) -> tuple[str | None, str]:
    prefix = "/memory-dashboard/"
    if not path.startswith(prefix):
        return None, ""
    rest = path[len(prefix):]
    if not rest:
        return None, ""
    block_key, _, suffix = rest.partition("/")
    if not block_key:
        return None, ""
    return block_key, f"/{suffix}" if suffix else "/"


def _is_memory_dashboard_root_asset(path: str) -> bool:
    if not path.startswith("/") or path.startswith(("/api/", "/memory-dashboard/", "/dashboard/")):
        return False
    leaf = path.rsplit("/", 1)[-1]
    if "." not in leaf:
        return False
    suffix = "." + leaf.rsplit(".", 1)[-1].lower()
    return suffix in MEMORY_DASHBOARD_ROOT_ASSET_SUFFIXES


def _upstream_path(repo_key: str, suffix: str) -> str:
    return f"/dashboard/{repo_key}{suffix or '/'}"


def _repo_key_from_referer(raw_headers: Any) -> str | None:
    for name, value in raw_headers:
        if bytes(name).lower() != b"referer":
            continue
        referer = bytes(value).decode("latin-1")
        repo_key, _ = _match_dashboard_path(urlsplit(referer).path)
        return repo_key
    return None


def _memory_key_from_referer(raw_headers: Any) -> str | None:
    for name, value in raw_headers:
        if bytes(name).lower() != b"referer":
            continue
        referer = bytes(value).decode("latin-1")
        block_key, _ = _match_memory_dashboard_path(urlsplit(referer).path)
        return block_key
    return None


def _repo_key_from_token(
    query_string: bytes | str,
    token_resolver: Callable[[str], str | None],
) -> str | None:
    query = query_string.decode("latin-1") if isinstance(query_string, bytes) else query_string
    token = parse_qs(query, keep_blank_values=False).get("token", [None])[0]
    if not token:
        return None
    return token_resolver(token)


async def _proxy_stream_response(
    scope: Scope,
    receive: Receive,
    send: Send,
    *,
    upstream_path: str,
    target: str,
    location_prefix: str,
    location_key: str,
    error_label: str,
) -> None:
    """Forward a request to a dashboard upstream, streaming the response back.

    Uses ``client.stream`` so long-lived responses (notably the SSE ``/stream``
    endpoint) are forwarded chunk-by-chunk as they arrive. Buffering the whole
    body (``client.request`` + ``response.content``) hangs on a never-ending
    stream until the read timeout, then surfaces as a 502.
    """
    body = await _read_body(receive)
    target_parts = urlsplit(target)
    query = scope.get("query_string", b"").decode("latin-1")
    upstream_url = urlunsplit((
        target_parts.scheme,
        target_parts.netloc,
        upstream_path,
        query,
        "",
    ))
    headers = _forward_headers(scope.get("headers", []), target_parts.netloc)
    method = str(scope.get("method", "GET"))
    # ``read=None`` keeps idle SSE connections alive; connect/write/pool stay
    # bounded so a misbehaving upstream still fails fast.
    timeout = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)

    start_sent = False
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=timeout) as client:
            async with client.stream(method, upstream_url, content=body, headers=headers) as response:
                response_headers = _response_headers_for_prefix(
                    response.headers,
                    prefix=location_prefix,
                    key=location_key,
                    target=target,
                )
                await send({
                    "type": "http.response.start",
                    "status": response.status_code,
                    "headers": response_headers,
                })
                start_sent = True
                # Long-lived responses (SSE) outlive the request body, so keep
                # watching ``receive`` for client disconnect — otherwise the upstream
                # connection is held open for a stream nobody is consuming.
                disconnected = asyncio.Event()
                drain_task = asyncio.create_task(_await_disconnect(receive, disconnected))
                try:
                    async for chunk in response.aiter_raw():
                        if disconnected.is_set():
                            break
                        await send({"type": "http.response.body", "body": chunk, "more_body": True})
                finally:
                    drain_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await drain_task
                    # Always close the response, even if the upstream errors or the
                    # client disconnects mid-stream, so the downstream never hangs.
                    with suppress(Exception):
                        await send({"type": "http.response.body", "body": b""})
    except httpx.HTTPError as exc:
        if not start_sent:
            await _send_plain(send, 502, f"{error_label}: {exc}".encode("utf-8"))


async def _await_disconnect(receive: Receive, disconnected: asyncio.Event) -> None:
    while True:
        message = await receive()
        if message.get("type") == "http.disconnect":
            disconnected.set()
            return


async def _read_body(receive: Receive) -> bytes:
    chunks: list[bytes] = []
    more_body = True
    while more_body:
        message = await receive()
        if message["type"] != "http.request":
            continue
        body = message.get("body", b"")
        if body:
            chunks.append(body)
        more_body = bool(message.get("more_body", False))
    return b"".join(chunks)


def _forward_headers(raw_headers: Any, host: str) -> dict[str, str]:
    headers: dict[str, str] = {"host": host, "accept-encoding": "identity"}
    for name, value in raw_headers:
        lower = bytes(name).lower()
        if lower in HOP_BY_HOP_HEADERS or lower in {b"host", b"accept-encoding"}:
            continue
        headers[bytes(name).decode("latin-1")] = bytes(value).decode("latin-1")
    return headers


def _response_headers_for_prefix(
    headers: httpx.Headers,
    *,
    prefix: str,
    key: str,
    target: str,
) -> list[tuple[bytes, bytes]]:
    rewritten: list[tuple[bytes, bytes]] = []
    for name, value in headers.multi_items():
        lower = name.lower().encode("latin-1")
        if lower in RESPONSE_HEADERS_TO_DROP:
            continue
        if name.lower() == "location":
            value = _rewrite_prefixed_location(value, prefix=prefix, key=key, target=target)
        rewritten.append((name.encode("latin-1"), value.encode("latin-1")))
    return rewritten


def _rewrite_location(location: str, repo_key: str, target: str) -> str:
    return _rewrite_prefixed_location(location, prefix="/dashboard", key=repo_key, target=target)


def _rewrite_prefixed_location(location: str, *, prefix: str, key: str, target: str) -> str:
    base_path = f"{prefix}/{key}/"
    target_parts = urlsplit(target)
    location_parts = urlsplit(location)

    if location_parts.scheme or location_parts.netloc:
        if (
            location_parts.scheme not in {"http", "https"}
            or location_parts.netloc != target_parts.netloc
        ):
            return location
        path = location_parts.path or "/"
        query = location_parts.query
        fragment = location_parts.fragment
    else:
        path = location_parts.path or "/"
        query = location_parts.query
        fragment = location_parts.fragment

    if path == base_path or path.startswith(base_path):
        rewritten_path = path
    elif path == "/":
        rewritten_path = base_path
    elif path.startswith("/"):
        rewritten_path = f"{prefix}/{key}{path}"
    else:
        rewritten_path = f"{base_path}{path}"
    return urlunsplit(("", "", rewritten_path, query, fragment))


async def _send_plain(send: Send, status: int, body: bytes) -> None:
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"text/plain; charset=utf-8")],
    })
    await send({"type": "http.response.body", "body": body})
