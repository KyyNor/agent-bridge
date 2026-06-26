from __future__ import annotations

import asyncio
from contextlib import suppress

import httpx

from agent_bridge.api.dashboard_proxy import (
    DashboardProxyMiddleware,
    MemoryDashboardProxyMiddleware,
    _match_dashboard_path,
    _match_memory_dashboard_path,
    _memory_key_from_referer,
    _repo_key_from_referer,
    _repo_key_from_token,
    _rewrite_location,
    _rewrite_prefixed_location,
    _upstream_path,
)
from agent_bridge.knowledge_management.code_knowledge.dashboard_urls import external_dashboard_url


def test_external_dashboard_url_keeps_token_without_exposing_vite_port() -> None:
    url = external_dashboard_url("headroom", "http://127.0.0.1:48001/?token=abc")

    assert url == "/dashboard/headroom/?token=abc"


def test_dashboard_proxy_rewrites_vite_root_redirect_to_repo_base() -> None:
    location = _rewrite_location("/", "headroom", "http://127.0.0.1:48001/?token=abc")

    assert location == "/dashboard/headroom/"


def test_dashboard_proxy_does_not_double_rewrite_vite_base_redirect() -> None:
    location = _rewrite_location(
        "/dashboard/headroom/?token=abc&theme=dark",
        "headroom",
        "http://127.0.0.1:48001/dashboard/headroom/?token=abc",
    )

    assert location == "/dashboard/headroom/?token=abc&theme=dark"


def test_dashboard_proxy_rewrites_absolute_vite_asset_redirect() -> None:
    location = _rewrite_location(
        "http://127.0.0.1:48001/assets/app.js",
        "headroom",
        "http://127.0.0.1:48001/?token=abc",
    )

    assert location == "/dashboard/headroom/assets/app.js"


def test_dashboard_proxy_extracts_repo_and_strips_prefix() -> None:
    assert _match_dashboard_path("/dashboard/headroom/assets/app.js") == ("headroom", "/assets/app.js")
    assert _match_dashboard_path("/dashboard/headroom/") == ("headroom", "/")
    assert _match_dashboard_path("/code-repo/repositories") == (None, "")


def test_memory_dashboard_proxy_extracts_block_and_strips_prefix() -> None:
    assert _match_memory_dashboard_path("/memory-dashboard/test-mem/assets/app.js") == ("test-mem", "/assets/app.js")
    assert _match_memory_dashboard_path("/memory-dashboard/test-mem/") == ("test-mem", "/")
    assert _match_memory_dashboard_path("/memory/blocks") == (None, "")


def test_memory_dashboard_proxy_rewrites_worker_root_redirect_to_block_base() -> None:
    location = _rewrite_prefixed_location(
        "/",
        prefix="/memory-dashboard",
        key="test-mem",
        target="http://127.0.0.1:48100/",
    )

    assert location == "/memory-dashboard/test-mem/"


def test_dashboard_proxy_keeps_vite_base_for_upstream_module_requests() -> None:
    assert _upstream_path("headroom", "/@vite/client") == "/dashboard/headroom/@vite/client"
    assert _upstream_path("headroom", "/src/main.tsx") == "/dashboard/headroom/src/main.tsx"


def test_dashboard_proxy_can_route_root_data_endpoints_from_referer() -> None:
    headers = [(b"referer", b"http://127.0.0.1:8765/dashboard/headroom/?token=abc&theme=dark")]

    assert _repo_key_from_referer(headers) == "headroom"


def test_memory_dashboard_proxy_can_route_api_requests_from_referer() -> None:
    headers = [(b"referer", b"http://127.0.0.1:8765/memory-dashboard/test-mem/?theme=dark")]

    assert _memory_key_from_referer(headers) == "test-mem"


def test_dashboard_proxy_can_route_root_data_endpoints_from_token_without_referer() -> None:
    assert _repo_key_from_token(b"token=abc", {"abc": "headroom"}.get) == "headroom"


def test_dashboard_proxy_uses_token_resolver_for_root_data_endpoint_without_referer(monkeypatch) -> None:
    app_called = False
    calls = []

    async def app(scope, receive, send):
        nonlocal app_called
        app_called = True

    async def fake_proxy_http(self, scope, receive, send, repo_key, upstream_path, target):
        calls.append((repo_key, upstream_path, target))

    monkeypatch.setattr(DashboardProxyMiddleware, "_proxy_http", fake_proxy_http)
    middleware = DashboardProxyMiddleware(
        app,
        target_resolver={"headroom": "http://127.0.0.1:48000/?token=abc"}.get,
        token_resolver={"abc": "headroom"}.get,
    )

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/knowledge-graph.json",
        "query_string": b"token=abc",
        "headers": [],
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        pass

    asyncio.run(middleware(scope, receive, send))

    assert app_called is False
    assert calls == [("headroom", "/knowledge-graph.json", "http://127.0.0.1:48000/?token=abc")]


def test_memory_dashboard_proxy_routes_worker_api_requests_from_referer(monkeypatch) -> None:
    app_called = False
    calls = []

    async def app(scope, receive, send):
        nonlocal app_called
        app_called = True

    async def fake_proxy_http(self, scope, receive, send, block_key, upstream_path, target):
        calls.append((block_key, upstream_path, target))

    monkeypatch.setattr(MemoryDashboardProxyMiddleware, "_proxy_http", fake_proxy_http)
    middleware = MemoryDashboardProxyMiddleware(
        app,
        target_resolver={"test-mem": "http://127.0.0.1:48100/"}.get,
    )

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/observations",
        "query_string": b"",
        "headers": [(b"referer", b"http://127.0.0.1:8765/memory-dashboard/test-mem/")],
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        pass

    asyncio.run(middleware(scope, receive, send))

    assert app_called is False
    assert calls == [("test-mem", "/api/observations", "http://127.0.0.1:48100/")]


def test_memory_dashboard_proxy_routes_root_static_assets_from_referer(monkeypatch) -> None:
    app_called = False
    calls = []

    async def app(scope, receive, send):
        nonlocal app_called
        app_called = True

    async def fake_proxy_http(self, scope, receive, send, block_key, upstream_path, target):
        calls.append((block_key, upstream_path, target))

    monkeypatch.setattr(MemoryDashboardProxyMiddleware, "_proxy_http", fake_proxy_http)
    middleware = MemoryDashboardProxyMiddleware(
        app,
        target_resolver={"test-mem": "http://127.0.0.1:48100/"}.get,
    )

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/icon-thick-investigated.svg",
        "query_string": b"",
        "headers": [(b"referer", b"http://127.0.0.1:8765/memory-dashboard/test-mem/")],
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        pass

    asyncio.run(middleware(scope, receive, send))

    assert app_called is False
    assert calls == [("test-mem", "/icon-thick-investigated.svg", "http://127.0.0.1:48100/")]


async def _noop_app(scope, receive, send):  # pragma: no cover - routing should never reach the app
    raise AssertionError("downstream app must not be called for proxied paths")


def _assert_proxy_streams_sse_first_event(middleware, scope, respx_mock, upstream_url) -> None:
    # SSE upstream: yields one event, then never ends (the connection stays open,
    # exactly like the claude-mem dashboard /stream). A buffering proxy hangs
    # forever reading the full body; a streaming proxy must forward the first event
    # promptly while the upstream is still open.
    first_event = b'data: {"type":"connected"}\n\n'
    never_finish = asyncio.Event()

    async def upstream_stream():
        yield first_event
        await never_finish.wait()

    respx_mock.get(upstream_url).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/event-stream", "cache-control": "no-cache"},
            stream=upstream_stream(),
        )
    )

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    body_sent = False

    async def receive():
        nonlocal body_sent
        if not body_sent:
            body_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        # Real ASGI blocks here until the client disconnects; in this window it
        # never does, so block forever (the task is cancelled by the driver).
        await never_finish.wait()
        return {"type": "http.disconnect"}

    async def driver():
        task = asyncio.create_task(middleware(scope, receive, send))
        # Give a streaming proxy ample time to forward the first event; a buffering
        # proxy is still blocked reading the never-ending body at this point.
        await asyncio.sleep(0.3)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(driver())

    starts = [m for m in sent if m.get("type") == "http.response.start"]
    assert starts and starts[0]["status"] == 200
    forwarded = b"".join(m.get("body", b"") for m in sent if m.get("type") == "http.response.body")
    assert first_event in forwarded


def test_memory_dashboard_proxy_streams_sse_events_without_buffering(respx_mock) -> None:
    middleware = MemoryDashboardProxyMiddleware(
        app=_noop_app,
        target_resolver={"test-mem": "http://127.0.0.1:48100/"}.get,
    )
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/stream",
        "query_string": b"",
        "headers": [(b"referer", b"http://127.0.0.1:8765/memory-dashboard/test-mem/")],
    }
    _assert_proxy_streams_sse_first_event(
        middleware, scope, respx_mock, "http://127.0.0.1:48100/stream"
    )


def test_codegraph_dashboard_proxy_streams_sse_events_without_buffering(respx_mock) -> None:
    middleware = DashboardProxyMiddleware(
        app=_noop_app,
        target_resolver={"headroom": "http://127.0.0.1:48000/"}.get,
    )
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/dashboard/headroom/stream",
        "query_string": b"",
        "headers": [],
    }
    _assert_proxy_streams_sse_first_event(
        middleware, scope, respx_mock, "http://127.0.0.1:48000/dashboard/headroom/stream"
    )


def test_memory_dashboard_proxy_returns_502_when_upstream_is_unreachable(respx_mock) -> None:
    respx_mock.get("http://127.0.0.1:48100/stream").mock(
        side_effect=httpx.ConnectError("connection refused")
    )

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    middleware = MemoryDashboardProxyMiddleware(
        app=_noop_app,
        target_resolver={"test-mem": "http://127.0.0.1:48100/"}.get,
    )
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/stream",
        "query_string": b"",
        "headers": [(b"referer", b"http://127.0.0.1:8765/memory-dashboard/test-mem/")],
    }

    asyncio.run(middleware(scope, receive, send))

    starts = [m for m in sent if m.get("type") == "http.response.start"]
    assert starts and starts[0]["status"] == 502
    body = b"".join(m.get("body", b"") for m in sent if m.get("type") == "http.response.body")
    # Pre-streaming behaviour: the 502 body carries the proxy label + underlying
    # error so failures stay greppable in logs.
    assert b"memory dashboard proxy failed" in body


def test_memory_dashboard_proxy_stops_reading_upstream_when_client_disconnects(respx_mock) -> None:
    pull_count = 0

    async def upstream_stream():
        nonlocal pull_count
        while True:
            pull_count += 1
            yield b"data: tick\n\n"
            await asyncio.sleep(0.02)

    respx_mock.get("http://127.0.0.1:48100/stream").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=upstream_stream(),
        )
    )

    async def send(message):
        pass

    receive_calls = 0

    async def receive():
        nonlocal receive_calls
        receive_calls += 1
        if receive_calls == 1:
            return {"type": "http.request", "body": b"", "more_body": False}
        # Client disconnects a moment into the stream.
        await asyncio.sleep(0.1)
        return {"type": "http.disconnect"}

    middleware = MemoryDashboardProxyMiddleware(
        app=_noop_app,
        target_resolver={"test-mem": "http://127.0.0.1:48100/"}.get,
    )
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/stream",
        "query_string": b"",
        "headers": [(b"referer", b"http://127.0.0.1:8765/memory-dashboard/test-mem/")],
    }

    async def driver():
        task = asyncio.create_task(middleware(scope, receive, send))
        await asyncio.sleep(0.6)
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    asyncio.run(driver())

    # A proxy that honours disconnect stops consuming the upstream shortly after
    # the client goes away (~5 ticks in 0.1s). One that ignores disconnect keeps
    # pulling forever (~30 ticks in 0.6s) and leaks the upstream connection.
    assert 1 <= pull_count < 15, f"upstream kept being read after disconnect: {pull_count} pulls"
