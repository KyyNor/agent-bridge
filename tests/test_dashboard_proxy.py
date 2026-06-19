from __future__ import annotations

import asyncio

from agent_bridge.api.dashboard_proxy import (
    DashboardProxyMiddleware,
    _match_dashboard_path,
    _repo_key_from_referer,
    _repo_key_from_token,
    _rewrite_location,
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


def test_dashboard_proxy_keeps_vite_base_for_upstream_module_requests() -> None:
    assert _upstream_path("headroom", "/@vite/client") == "/dashboard/headroom/@vite/client"
    assert _upstream_path("headroom", "/src/main.tsx") == "/dashboard/headroom/src/main.tsx"


def test_dashboard_proxy_can_route_root_data_endpoints_from_referer() -> None:
    headers = [(b"referer", b"http://127.0.0.1:8765/dashboard/headroom/?token=abc&theme=dark")]

    assert _repo_key_from_referer(headers) == "headroom"


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
