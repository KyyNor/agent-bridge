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
    is_blocked_asset,
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


def _pi_adapter():
    from agent_bridge.agent_workspace.adapters.pi import PiWorkspaceAdapter

    return PiWorkspaceAdapter(configs=None, mcp_url="http://127.0.0.1:8080/mcp")


def _dsh_adapter():
    from agent_bridge.agent_workspace.adapters.dsh import DshWorkspaceAdapter

    return DshWorkspaceAdapter(configs=None, mcp_url="http://127.0.0.1:8080/mcp")


@pytest.mark.parametrize("path", ["/sw.js", "/sw.js?v=123", "/service-worker.js", "/workbox.js"])
def test_blocked_pi_service_worker_paths(path: str) -> None:
    assert is_blocked_asset(_pi_adapter(), path) is True


@pytest.mark.parametrize(
    "path", ["/", "/login", "/api/home", "/_next/static/chunks/a.js", "/icons/icon-192.png"]
)
def test_normal_pi_paths_not_blocked(path: str) -> None:
    assert is_blocked_asset(_pi_adapter(), path) is False


def test_dsh_blocks_nothing() -> None:
    """DSH 不声明拦截资源（由 adapter 而非代理硬编码）。"""
    for path in ("/sw.js", "/", "/api/home"):
        assert is_blocked_asset(_dsh_adapter(), path) is False


def test_proxy_auth_dispatch_is_adapter_driven() -> None:
    """鉴权分发由 adapter 声明，代理不再按 agent 写死分支。"""
    assert _pi_adapter().needs_proxy_auth() is True
    assert _dsh_adapter().needs_proxy_auth() is False


# -- Cookie 收敛与鉴权 --


def test_pi_request_cookies_only_forward_pi_scoped_cookies() -> None:
    header = "agent_bridge_admin=admin-secret; pi_web_session=abc; posthog=xyz"
    kept = _pi_adapter().filter_request_cookies(header, authority="127.0.0.1:48500")
    assert kept == "pi_web_session=abc"
    # 平台会话与统计 Cookie 不透传给 pi-web
    assert "admin-secret" not in kept
    assert "posthog" not in kept


def test_pi_request_cookies_returns_none_when_empty() -> None:
    adapter = _pi_adapter()
    assert adapter.filter_request_cookies("agent_bridge_admin=x", authority="a:1") is None
    assert adapter.filter_request_cookies(None, authority="a:1") is None


def test_merge_pi_session_cookie_replaces_stale_value() -> None:
    from agent_bridge.agent_workspace.adapters.pi import _merge_pi_session_cookie

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


# -- 上游鉴权解析（代理 → adapter）--


class _FakePiRuntime:
    def __init__(self, password: str | None = "pw") -> None:
        self.password = password
        self.calls: list[tuple[str, str]] = []

    def workspace_password(self, user_id: str, *, scope: str) -> str | None:
        self.calls.append((user_id, scope))
        return self.password


class _FakeBridgeService:
    """代理传给 adapter 的是装配根（AgentBridgeService），不是 runtime。"""

    def __init__(self, runtime: _FakePiRuntime) -> None:
        self.runtime = runtime

    def workspace_runtime(self, agent: str):
        assert agent == "pi"
        return self.runtime


def _resolve(adapter, service, *, target="http://127.0.0.1:48500", browser_cookie=None):
    import asyncio

    return asyncio.run(
        adapter.resolve_upstream_auth(
            service,
            user_id="alice",
            target=target,
            workspace_scope="personal",
            browser_cookie=browser_cookie,
        )
    )


def test_resolve_upstream_auth_uses_bridge_service_runtime(monkeypatch) -> None:
    """回归：代理传入装配根时必须经 ``workspace_runtime('pi')`` 取密码并完成换取。"""
    from agent_bridge.agent_workspace.adapters import pi as pi_module

    exchanged: list[tuple[str, str]] = []
    monkeypatch.setattr(
        pi_module,
        "exchange_session_cookie",
        lambda target, password: exchanged.append((target, password)) or "sess",
    )
    runtime = _FakePiRuntime()
    cookie = _resolve(_pi_adapter(), _FakeBridgeService(runtime), browser_cookie="pi_other=keep")

    assert cookie is not None
    assert f"{PI_SESSION_COOKIE}=sess" in cookie
    assert "pi_other=keep" in cookie
    assert runtime.calls == [("alice", "personal")]
    assert exchanged == [("http://127.0.0.1:48500", "pw")]


def test_resolve_upstream_auth_caches_session_per_runtime(monkeypatch) -> None:
    """同一实例只换取一次：逐请求换取会给每个资源请求叠加一次上游登录。"""
    from agent_bridge.agent_workspace.adapters import pi as pi_module

    exchanged: list[str] = []
    monkeypatch.setattr(
        pi_module,
        "exchange_session_cookie",
        lambda target, password: exchanged.append(password) or f"sess-{password}",
    )
    adapter = _pi_adapter()
    runtime = _FakePiRuntime()
    service = _FakeBridgeService(runtime)

    for _ in range(5):
        assert f"{PI_SESSION_COOKIE}=sess-pw" in _resolve(adapter, service)
    assert exchanged == ["pw"]

    # 实例重启（同端口、新密码）：缓存键变化，重新换取并丢弃旧条目。
    runtime.password = "pw2"
    assert f"{PI_SESSION_COOKIE}=sess-pw2" in _resolve(adapter, service)
    assert exchanged == ["pw", "pw2"]
    assert len(adapter._session_cache) == 1


def test_resolve_upstream_auth_does_not_cache_failures(monkeypatch) -> None:
    from agent_bridge.agent_workspace.adapters import pi as pi_module

    results = iter([None, "sess"])
    monkeypatch.setattr(pi_module, "exchange_session_cookie", lambda target, password: next(results))
    adapter = _pi_adapter()
    service = _FakeBridgeService(_FakePiRuntime())

    assert _resolve(adapter, service) is None
    assert f"{PI_SESSION_COOKIE}=sess" in _resolve(adapter, service)


def test_resolve_upstream_auth_without_password_skips_exchange(monkeypatch) -> None:
    from agent_bridge.agent_workspace.adapters import pi as pi_module

    monkeypatch.setattr(
        pi_module,
        "exchange_session_cookie",
        lambda *a, **k: pytest.fail("未运行实例不应发起换取"),
    )
    assert _resolve(_pi_adapter(), _FakeBridgeService(_FakePiRuntime(password=None))) is None


def httpx_headers(pairs: list[str]):
    import httpx

    return httpx.Headers([("set-cookie", value) for value in pairs])