"""Pi 工作台代理层测试（issue #17）。

覆盖 spike 确认的关键约束：

- **鉴权不能靠注入 Basic Auth**：pi-web 的页面请求只认 ``pi_web_session``
  Cookie，代理必须先用状态文件里的密码换取 Cookie 再转发；
- **service worker 必须拦截**：pi-web 注册 ``scope: "/"`` 的 ``/sw.js``，
  ``clients.claim()`` 会接管整个源站（含本平台 dashboard 与 DSH 工作台）；
- Cookie 收敛：平台会话（``agent_bridge_admin``）不透传给 pi-web；
- 路由：``/agent-workspace-pi`` 与 ``/agent-workspace-pi-shared`` 独立于 DSH。
"""

from __future__ import annotations

import pytest

from agent_bridge.api.workspace_proxy import (
    PROXY_PREFIX_ROUTES,
    RESERVED_PATH_PREFIXES,
    is_blocked_pi_asset,
    match_workspace_route,
    workspace_route_for_referer,
)
from agent_bridge.agent_workspace.adapters.pi import (
    PI_PERSONAL_PROXY_PREFIX,
    PI_SHARED_PROXY_PREFIX,
    PI_SESSION_COOKIE,
)


# -- 路由 --


def test_pi_prefixes_route_independently() -> None:
    assert PROXY_PREFIX_ROUTES[PI_PERSONAL_PROXY_PREFIX] == ("pi", "personal")
    assert PROXY_PREFIX_ROUTES[PI_SHARED_PROXY_PREFIX] == ("pi", "shared")


def test_shared_prefix_matches_before_personal() -> None:
    """``/agent-workspace-pi-shared`` 必须先于 ``/agent-workspace-pi`` 命中。"""
    assert match_workspace_route("/agent-workspace-pi-shared") == ("pi", "shared", "/")
    assert match_workspace_route("/agent-workspace-pi-shared/api/home") == (
        "pi", "shared", "/api/home",
    )
    assert match_workspace_route("/agent-workspace-pi") == ("pi", "personal", "/")
    # DSH 既有前缀语义不变
    assert match_workspace_route("/agent-workspace") == ("dsh", "personal", "/")
    assert match_workspace_route("/agent-workspace-shared/x") == ("dsh", "shared", "/x")


def test_pi_prefixes_are_reserved_from_escape_claim() -> None:
    """Pi 前缀不能被根路径逃逸逻辑认领走，必须自己走前缀路由。"""
    assert any(prefix.startswith("/agent-workspace-pi") for prefix in RESERVED_PATH_PREFIXES)


def test_referer_routes_per_agent() -> None:
    assert workspace_route_for_referer("/agent-workspace-pi/chat") == ("pi", "personal")
    assert workspace_route_for_referer("/agent-workspace-pi-shared/x") == ("pi", "shared")
    assert workspace_route_for_referer("/agent-workspace/x") == ("dsh", "personal")
    assert workspace_route_for_referer("/other") is None


# -- service worker 拦截 --


@pytest.mark.parametrize("path", ["/sw.js", "/sw.js?v=123", "/service-worker.js", "/workbox.js"])
def test_blocked_pi_service_worker_paths(path: str) -> None:
    assert is_blocked_pi_asset(path) is True


@pytest.mark.parametrize(
    "path", ["/", "/login", "/api/home", "/_next/static/chunks/a.js", "/icons/icon-192.png"]
)
def test_normal_pi_paths_not_blocked(path: str) -> None:
    assert is_blocked_pi_asset(path) is False


# -- Cookie 收敛与鉴权 --


def test_pi_request_cookies_only_forward_pi_scoped_cookies() -> None:
    from agent_bridge.api.workspace_proxy import _pi_request_cookies

    header = "agent_bridge_admin=admin-secret; pi_web_session=abc; posthog=xyz"
    kept = _pi_request_cookies(header)
    assert kept == "pi_web_session=abc"
    # 平台会话与统计 Cookie 不透传给 pi-web
    assert "admin-secret" not in kept
    assert "posthog" not in kept


def test_pi_request_cookies_returns_none_when_empty() -> None:
    from agent_bridge.api.workspace_proxy import _pi_request_cookies

    assert _pi_request_cookies("agent_bridge_admin=x") is None
    assert _pi_request_cookies(None) is None


def test_merge_pi_session_cookie_replaces_stale_value() -> None:
    from agent_bridge.api.workspace_proxy import _merge_pi_session_cookie

    merged = _merge_pi_session_cookie(
        "pi_web_session=old; pi_other=keep", pair=f"{PI_SESSION_COOKIE}=fresh"
    )
    assert f"{PI_SESSION_COOKIE}=fresh" in merged
    assert "old" not in merged
    assert "pi_other=keep" in merged


def test_exchange_session_cookie_returns_value(monkeypatch) -> None:
    from agent_bridge.agent_workspace.adapters import pi as pi_module

    class FakeResponse:
        status_code = 200
        headers = httpx_headers(
            [
                f"{PI_SESSION_COOKIE}=v1.100.abc.sig; Path=/; HttpOnly; SameSite=lax",
                "other=1",
            ]
        )

    monkeypatch.setattr(pi_module.httpx, "post", lambda *a, **k: FakeResponse())
    value = pi_module.exchange_session_cookie("http://127.0.0.1:48500", "pw")
    assert value == "v1.100.abc.sig"


def test_exchange_session_cookie_handles_failure(monkeypatch) -> None:
    from agent_bridge.agent_workspace.adapters import pi as pi_module

    class Denied:
        status_code = 401
        headers = httpx_headers([])

    monkeypatch.setattr(pi_module.httpx, "post", lambda *a, **k: Denied())
    assert pi_module.exchange_session_cookie("http://127.0.0.1:48500", "pw") is None


def test_exchange_session_cookie_never_sends_basic_auth(monkeypatch) -> None:
    """换取请求不得携带 Authorization。

    pi-web 的失败限流是**单一全局计数器**且成功不重置，任何一次错误尝试都会
    把整实例（共享时影响全组）锁死最长 60 秒。
    """
    from agent_bridge.agent_workspace.adapters import pi as pi_module

    captured: dict = {}

    class Ok:
        status_code = 200
        headers = httpx_headers([f"{PI_SESSION_COOKIE}=v; Path=/"])

    def fake_post(url, **kwargs):
        captured.update(kwargs)
        return Ok()

    monkeypatch.setattr(pi_module.httpx, "post", fake_post)
    pi_module.exchange_session_cookie("http://127.0.0.1:48500", "pw")
    assert "headers" not in captured or "Authorization" not in captured.get("headers", {})


def httpx_headers(pairs: list[str]):
    import httpx

    return httpx.Headers([("set-cookie", value) for value in pairs])