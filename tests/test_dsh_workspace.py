"""DSH Workspace 能力平面接入与反向代理测试。"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import types
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from agent_bridge.access_control.identity import IdentityConfig, RequestIdentityResolver
from agent_bridge.api.workspace_proxy import (
    AgentWorkspaceProxyMiddleware,
    dsh_session_cookie_name,
    is_reserved_path,
    match_workspace_path,
    rewrite_workspace_location,
    workspace_escape_path,
)
from agent_bridge.dsh.workspace import (
    DSH_CAPABILITY_HEADER,
    DshWorkspaceCapabilityRegistry,
    mcp_overlay_path,
)


# -- capability registry --


def test_workspace_capability_issue_require_and_user_exclusivity() -> None:
    registry = DshWorkspaceCapabilityRegistry()
    capability = registry.issue(user_id="user1", profile_key="safe", owner_group_key="groupa")
    assert registry.require(capability.token) is capability
    assert registry.require(capability.token, profile_key="safe") is capability

    with pytest.raises(Exception):
        registry.require(capability.token, profile_key="other")
    with pytest.raises(Exception):
        registry.require("not-a-token")

    # 同一用户重新签发会使旧 capability 失效
    second = registry.issue(user_id="user1", profile_key="safe", owner_group_key="groupa")
    with pytest.raises(Exception):
        registry.require(capability.token)
    assert registry.require(second.token) is second

    registry.revoke_for_user("user1")
    with pytest.raises(Exception):
        registry.require(second.token)


def test_workspace_capability_expires(monkeypatch) -> None:
    registry = DshWorkspaceCapabilityRegistry()
    now = {"monotonic": 1000.0}
    monkeypatch.setattr("agent_bridge.dsh.workspace.time.monotonic", lambda: now["monotonic"])
    capability = registry.issue(
        user_id="user1", profile_key="safe", owner_group_key="groupa", ttl_seconds=10
    )
    assert registry.require(capability.token) is capability
    now["monotonic"] += 11
    with pytest.raises(Exception):
        registry.require(capability.token)


# -- 共享 fixture --


class FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode = None

    def poll(self) -> int | None:
        return self.returncode


class FakeLauncher:
    def __init__(self, pid: int = 420001) -> None:
        self.next_pid = pid
        self.starts: list[dict] = []

    def start(self, *, command, env, cwd, log_path, identity):
        process = FakeProcess(self.next_pid)
        self.next_pid += 1
        port = 0
        if "--port" in command:
            try:
                port = int(command[command.index("--port") + 1])
            except (IndexError, ValueError):
                port = 0
        self.starts.append({"pid": process.pid, "command": list(command), "env": dict(env), "port": port})
        return process


@pytest.fixture
def service(wm_paths):
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.access.upsert_group(actor="root", group_key="groupa", name="A 组")
    svc.access.create_user(actor="root", user_id="user1")
    svc.access.set_user_group(actor="root", user_id="user1", group_key="groupa")
    # Profile 是组内资源：由 groupa 成员创建，避免归入 root 的维护组
    svc.governance.upsert_profile("user1", "safe", "安全平面", "", "active")
    svc.governance.upsert_profile("user1", "wide", "宽平面", "", "active")
    return svc


@pytest.fixture
def home(tmp_path):
    home = tmp_path / "linux-home"
    home.mkdir()
    return home


@pytest.fixture
def passwd_lookup(home):
    def lookup(user: str):
        return types.SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid(), pw_dir=str(home / user))

    return lookup


def configure_group(service, **overrides) -> None:
    """公共接入 + 组级配置（与 #2 的配置分层保持一致）。"""
    runtime_payload = {
        "web_command": "dsh web {patch} --host 127.0.0.1 --port {port} --no-open",
        "idle_timeout_minutes": 120,
        "base_url": "http://model.internal/v1",
        "available_models": ["gpt-x"],
    }
    runtime_payload.update(overrides)
    service.dsh_configs.save_runtime_config("root", **runtime_payload)
    service.dsh_configs.save_group_config(
        "root",
        group_key="groupa",
        linux_user="groupa",
        default_model="gpt-x",
        api_key="sk-secret",
    )


def install_fakes(service, home, passwd_lookup, monkeypatch) -> FakeLauncher:
    launcher = FakeLauncher()
    service.dsh._launcher = launcher
    service.dsh._passwd_lookup = passwd_lookup
    monkeypatch.setattr(
        service.dsh,
        "_pid_alive",
        staticmethod(lambda pid: any(start["pid"] == pid for start in launcher.starts)),
    )
    monkeypatch.setattr(
        service.dsh,
        "_probe_port",
        staticmethod(lambda port: any(int(start["port"]) == port for start in launcher.starts)),
    )
    return launcher


# -- authorize / MCP 注入 --


def test_authorize_rejects_profiles_without_access(service, home, passwd_lookup, monkeypatch) -> None:
    configure_group(service)
    service.access.upsert_group(actor="root", group_key="groupb", name="B 组")
    service.access.create_user(actor="root", user_id="user3")
    service.access.set_user_group(actor="root", user_id="user3", group_key="groupb")
    install_fakes(service, home, passwd_lookup, monkeypatch)

    with pytest.raises(Exception):
        service.dsh.authorize_workspace("user3", profile_key="safe")  # 未配置 DSH 的小组
    with pytest.raises(Exception):
        service.dsh.authorize_workspace("user1", profile_key="missing-profile")


def test_authorize_injects_mcp_overlay_and_switching_profile_restarts(
    service, home, passwd_lookup, monkeypatch
) -> None:
    configure_group(service)
    launcher = install_fakes(service, home, passwd_lookup, monkeypatch)

    result = service.dsh.authorize_workspace("user1", profile_key="safe")
    assert result["status"] == "running"
    assert result["profile_key"] == "safe"
    assert result["workspace_url"] == "/agent-workspace/"
    assert len(launcher.starts) == 1

    config_dir = home / "groupa" / ".config" / "dsh" / "user1"
    overlay_path = mcp_overlay_path(config_dir)
    assert overlay_path.exists()
    # 覆盖文件写入的是 DSH loader patch，而不是 mcpServers JSON
    overlay = yaml.safe_load(overlay_path.read_text(encoding="utf-8"))
    entry = overlay[0]["insert"][0]
    assert entry["id"] == "agent-bridge-mcp"
    assert entry["name"] == "@deepseek-ai/dsh-mcp-client"
    config = entry["config"]
    assert config["transport"] == "streamable-http"
    assert config["url"].endswith("/mcp")
    assert config["headers"]["X-Agent-Bridge-MetaMCP-Profile"] == "safe"
    token = config["headers"][DSH_CAPABILITY_HEADER]
    assert token
    assert oct(overlay_path.stat().st_mode & 0o777) == "0o600"

    # 启动命令带上该覆盖文件
    command = launcher.starts[0]["command"]
    assert "--patch" in command
    assert command[command.index("--patch") + 1] == str(overlay_path)
    state = service.dsh._read_state("user1")
    assert state["profile_key"] == "safe"
    assert state["config_dir"] == str(config_dir)

    # 同一平面重复授权：复用实例，仅刷新 capability
    service.dsh.capabilities.require(token)
    again = service.dsh.authorize_workspace("user1", profile_key="safe")
    assert again["status"] == "running"
    assert len(launcher.starts) == 1
    refreshed = yaml.safe_load(overlay_path.read_text(encoding="utf-8"))
    refreshed_token = refreshed[0]["insert"][0]["config"]["headers"][DSH_CAPABILITY_HEADER]
    assert refreshed_token != token
    with pytest.raises(Exception):
        service.dsh.capabilities.require(token)

    # 切换能力平面：回收重启
    switched = service.dsh.authorize_workspace("user1", profile_key="wide")
    assert switched["profile_key"] == "wide"
    assert len(launcher.starts) == 2
    assert service.dsh._read_state("user1")["profile_key"] == "wide"


def test_authorize_without_profile_enters_plain_workspace(service, home, passwd_lookup, monkeypatch) -> None:
    configure_group(service)
    launcher = install_fakes(service, home, passwd_lookup, monkeypatch)

    # 先带平面进入
    service.dsh.authorize_workspace("user1", profile_key="safe")
    config_dir = home / "groupa" / ".config" / "dsh" / "user1"
    overlay_path = mcp_overlay_path(config_dir)
    assert overlay_path.exists()

    # 不带平面进入：清除注入并重启（不再携带 --patch）
    plain = service.dsh.authorize_workspace("user1", profile_key=None)
    assert plain["status"] == "running"
    assert plain["profile_key"] is None
    assert len(launcher.starts) == 2
    assert "--patch" not in launcher.starts[1]["command"]
    assert not overlay_path.exists()
    assert service.dsh._read_state("user1")["profile_key"] is None

    # 首次进入就不选平面：不产生覆盖文件
    service.dsh.stop_runtime("user1")
    fresh = service.dsh.authorize_workspace("user1", profile_key="")
    assert fresh["profile_key"] is None
    assert not overlay_path.exists()


def test_stop_revokes_workspace_capability(service, home, passwd_lookup, monkeypatch) -> None:
    configure_group(service)
    install_fakes(service, home, passwd_lookup, monkeypatch)
    service.dsh.authorize_workspace("user1", profile_key="safe")
    overlay_path = mcp_overlay_path(home / "groupa" / ".config" / "dsh" / "user1")
    overlay = yaml.safe_load(overlay_path.read_text(encoding="utf-8"))
    token = overlay[0]["insert"][0]["config"]["headers"][DSH_CAPABILITY_HEADER]
    capability = service.dsh.capabilities.require(token)
    assert capability.user_id == "user1"
    service.dsh.stop_runtime("user1")
    with pytest.raises(Exception):
        service.dsh.capabilities.require(capability.token)


def test_authorize_api_endpoint_allows_omitting_profile(service, home, passwd_lookup, monkeypatch) -> None:
    from agent_bridge.api.app import create_app

    configure_group(service)
    client = TestClient(create_app(service.paths, {"root"}))
    app_service = client.app.state.agent_bridge_service
    install_fakes(app_service, home, passwd_lookup, monkeypatch)
    headers_user = {"X-Agent-Bridge-User": "user1"}

    response = client.post(
        "/api/v1/dsh/workspace/authorize", headers=headers_user, json={"profile_key": "safe"}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "running"
    assert payload["profile_key"] == "safe"
    assert "port" not in payload

    # 不传/传 null 表示不注入能力平面
    plain = client.post("/api/v1/dsh/workspace/authorize", headers=headers_user, json={})
    assert plain.status_code == 200
    assert plain.json()["profile_key"] is None

    denied = client.post(
        "/api/v1/dsh/workspace/authorize", headers=headers_user, json={"profile_key": "nope"}
    )
    assert denied.status_code == 404


# -- MetaMCP capability 入口 --


def _mcp_tools(client: TestClient, headers: dict) -> tuple[int, list[str]]:
    response = client.post(
        "/mcp",
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **headers},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    if response.status_code != 200:
        return response.status_code, []
    names = [tool["name"] for tool in response.json()["result"]["tools"]]
    return response.status_code, names


def test_metamcp_accepts_dsh_capability_with_profile_scoping(
    service, home, passwd_lookup, monkeypatch
) -> None:
    from agent_bridge.api.app import create_app

    service.business_ledgers.create_ledger(
        "user1",
        ledger_key="assets",
        name="资产台账",
        description="",
        fields=[{"field_key": "name", "name": "名称", "field_type": "text", "query_modes": ["contains"]}],
    )
    service.governance.set_resource_profiles("user1", "business_ledger", "assets", ["safe"])

    configure_group(service)
    client = TestClient(create_app(service.paths, {"root"}))
    app_service = client.app.state.agent_bridge_service
    install_fakes(app_service, home, passwd_lookup, monkeypatch)
    app_service.dsh.authorize_workspace("user1", profile_key="safe")
    overlay = yaml.safe_load(
        mcp_overlay_path(home / "groupa" / ".config" / "dsh" / "user1").read_text(encoding="utf-8")
    )
    token = overlay[0]["insert"][0]["config"]["headers"][DSH_CAPABILITY_HEADER]

    # capability 即身份：无需 Cookie/CLI Header，能力平面来自签发上下文
    status, tools = _mcp_tools(client, {DSH_CAPABILITY_HEADER: token})
    assert status == 200
    assert "query_business_ledger" in tools

    # 无效 capability 被拒绝
    status, _ = _mcp_tools(client, {DSH_CAPABILITY_HEADER: "forged-token"})
    assert status == 403

    # capability 与显式 profile 头不一致被拒绝
    status, _ = _mcp_tools(client, {DSH_CAPABILITY_HEADER: token, "X-Agent-Bridge-MetaMCP-Profile": "wide"})
    assert status == 403


# -- 反向代理：单元 --


def test_match_workspace_path_variants() -> None:
    assert match_workspace_path("/agent-workspace") == "/"
    assert match_workspace_path("/agent-workspace/") == "/"
    assert match_workspace_path("/agent-workspace/ws/chat") == "/ws/chat"
    assert match_workspace_path("/agent-workspace-other") is None
    assert match_workspace_path("/api/v1/dsh/runtime") is None


def test_rewrite_workspace_location() -> None:
    target = "http://127.0.0.1:48400"
    assert rewrite_workspace_location("/", target=target) == "/agent-workspace/"
    assert rewrite_workspace_location("/login?next=1", target=target) == "/agent-workspace/login?next=1"
    assert rewrite_workspace_location("http://127.0.0.1:48400/a", target=target) == "/agent-workspace/a"
    # 非目标源或外部地址不改写
    assert rewrite_workspace_location("https://example.com/a", target=target) == "https://example.com/a"
    assert rewrite_workspace_location("/agent-workspace/keep", target=target) == "/agent-workspace/keep"


def test_session_cookie_is_forwarded_without_path_rewrite() -> None:
    """会话 Cookie 必须覆盖整站：DSH 前端的 /api/** 等根绝对路径也要带 Cookie。"""
    from agent_bridge.api.workspace_proxy import _session_cookie_headers

    authority = "127.0.0.1:48400"
    name = dsh_session_cookie_name(authority)
    headers = httpx.Headers(
        {
            "set-cookie": f"{name}=v1.abc; Path=/; HttpOnly; SameSite=Strict",
        }
    )
    forwarded = _session_cookie_headers(headers, authority)
    assert forwarded == [
        (b"set-cookie", f"{name}=v1.abc; Path=/; HttpOnly; SameSite=Strict".encode("utf-8"))
    ]
    # 其他 Cookie（非 DSH 会话）不转发给浏览器
    other = httpx.Headers({"set-cookie": "session=abc; Path=/"})
    assert _session_cookie_headers(other, authority) == []


def test_dsh_request_cookies_narrows_to_dsh_scope() -> None:
    """发往 DSH 的 Cookie 只保留当前 authority 会话与 dsh- 前缀应用 Cookie。"""
    from agent_bridge.api.workspace_proxy import _dsh_request_cookies

    authority = "127.0.0.1:48400"
    name = dsh_session_cookie_name(authority)
    other_port = dsh_session_cookie_name("127.0.0.1:48399")
    header = f"{name}=v1; agent_bridge_admin=root; {other_port}=v2; ph_phc_x=1; dsh-theme=dark"
    assert _dsh_request_cookies(header, authority=authority) == f"{name}=v1; dsh-theme=dark"
    assert _dsh_request_cookies("agent_bridge_admin=root; ph_x=1", authority=authority) is None
    assert _dsh_request_cookies(None, authority=authority) is None
    assert _dsh_request_cookies("", authority=authority) is None


def test_refresh_workspace_auth_picks_latest_banner(service, tmp_path) -> None:
    """token 轮换后重扫日志以最后一条横幅为准，并幂等落盘。"""
    log = tmp_path / "dsh.log"
    log.write_text(
        "dsh web: http://127.0.0.1:48400/?token=first-token\n"
        "…连接重载…\n"
        "dsh web: http://127.0.0.1:48400/?token=second-token\n",
        encoding="utf-8",
    )
    service.dsh._write_state(
        "user1",
        {
            "user_id": "user1",
            "group_key": "groupa",
            "linux_user": "groupa",
            "pid": 424242,
            "port": 48400,
            "config_dir": "",
            "log_path": str(log),
            "profile_key": None,
            "started_at": time.time(),
            "last_access_at": time.time(),
            "auth_path": "/",
            "auth_query": "token=first-token",
        },
    )
    # 横幅已轮换：返回最新入口并更新状态
    assert service.dsh.refresh_workspace_auth("user1") == ("/", "token=second-token")
    assert service.dsh.workspace_auth("user1") == ("/", "token=second-token")
    # 横幅未再变化：幂等返回，不重复落盘语义
    assert service.dsh.refresh_workspace_auth("user1") == ("/", "token=second-token")


def test_workspace_escape_path_routing_rules() -> None:
    """DSH 前端的根绝对路径请求按 Referer / 同源 WS 归属工作台。"""

    def http_scope(path: str, referer: str | None) -> dict:
        headers = [(b"host", b"bridge.internal:8080")]
        if referer:
            headers.append((b"referer", referer.encode("utf-8")))
        return {"type": "http", "method": "GET", "path": path, "headers": headers}

    prefix_referer = "http://bridge.internal:8080/agent-workspace/"
    # 工作台页面的根绝对资源、插件模块与 API 请求
    for path in ("/assets/index.js", "/plugins/", "/api/session/list", "/manifest.webmanifest"):
        assert workspace_escape_path(http_scope(path, prefix_referer)) == path
    # 无 Referer（地址栏直达、非工作台页面）或 Referer 不在前缀下：不进代理
    assert workspace_escape_path(http_scope("/assets/index.js", None)) is None
    assert workspace_escape_path(http_scope("/assets/index.js", "http://bridge.internal:8080/agent-bridge/")) is None
    # 保留路径即便是工作台 Referer 也让给 Agent Bridge 自己
    assert workspace_escape_path(http_scope("/api/v1/dsh/runtime", prefix_referer)) is None
    assert workspace_escape_path(http_scope("/agent-bridge/assets/index.js", prefix_referer)) is None
    assert is_reserved_path("/api/v1/workflows")
    assert is_reserved_path("/health")
    assert not is_reserved_path("/api/session/list")

    # WebSocket 无 Referer：只认同源握手（Origin 与 Host 同 authority）
    ws_path = {"type": "websocket", "path": "/api/remote.mux", "headers": [(b"host", b"bridge.internal:8080")]}
    assert workspace_escape_path({**ws_path, "headers": [*ws_path["headers"], (b"origin", b"http://bridge.internal:8080")]}) == "/api/remote.mux"
    assert workspace_escape_path(ws_path) is None
    assert workspace_escape_path({**ws_path, "headers": [(b"origin", b"https://evil.example")]}) is None


# -- 反向代理：HTTP（直接驱动 middleware + respx） --


class _FakeDshService:
    def __init__(
        self,
        target: str | None,
        *,
        auth: tuple[str, str] | None = None,
        refreshed_auth: tuple[str, str] | None = None,
    ) -> None:
        self._target = target
        self._auth = auth
        self._refreshed_auth = refreshed_auth
        self.ensure_calls: list[str] = []
        self.touch_calls: list[str] = []
        self.auth_calls: list[str] = []
        self.refresh_calls: list[str] = []

    def require_runtime_target(self, user_id: str) -> str | None:
        if self._target is not None:
            self.touch_calls.append(user_id)
        return self._target

    def ensure_running(self, user_id: str) -> dict:
        self.ensure_calls.append(user_id)
        return {"status": "running"}

    def workspace_auth(self, user_id: str) -> tuple[str, str] | None:
        self.auth_calls.append(user_id)
        return self._auth

    def refresh_workspace_auth(self, user_id: str) -> tuple[str, str] | None:
        self.refresh_calls.append(user_id)
        return self._refreshed_auth if self._refreshed_auth is not None else self._auth


class _FakeBridgeService:
    def __init__(self, dsh: _FakeDshService) -> None:
        self.dsh = dsh


def _http_scope(path: str, user: str | None = "user1", query: str = "", **extra: str) -> dict:
    headers = []
    if user is not None:
        headers.append((b"x-agent-bridge-user", user.encode("utf-8")))
    for name, value in extra.items():
        headers.append((name.replace("_", "-").encode("latin-1"), value.encode("utf-8")))
    return {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": query.encode("utf-8"),
        "headers": headers,
    }


def _identity_resolver() -> RequestIdentityResolver:
    return RequestIdentityResolver(IdentityConfig())


def _drive(middleware, scope) -> tuple[int, dict[str, str], bytes]:
    result: dict = {}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            result["status"] = message["status"]
            result["headers"] = {
                bytes(k).decode("latin-1").lower(): bytes(v).decode("latin-1")
                for k, v in message.get("headers", [])
            }
        elif message["type"] == "http.response.body":
            result["body"] = result.get("body", b"") + message.get("body", b"")

    asyncio.run(middleware(scope, receive, send))
    return result.get("status", 0), result.get("headers", {}), result.get("body", b"")


async def _rejecting_app(scope, receive, send):  # pragma: no cover - 不应穿透
    raise AssertionError("workspace middleware must consume matched path")


def _middleware(dsh: _FakeDshService) -> AgentWorkspaceProxyMiddleware:
    return AgentWorkspaceProxyMiddleware(
        app=_rejecting_app,
        service=_FakeBridgeService(dsh),
        identity_resolver=_identity_resolver(),
    )


def test_workspace_proxy_requires_identity() -> None:
    middleware = _middleware(_FakeDshService(None))
    status, _, _ = _drive(middleware, _http_scope("/agent-workspace/", user=None))
    assert status == 401


def test_workspace_proxy_triggers_ensure_when_not_running() -> None:
    dsh = _FakeDshService(None)
    status, _, body = _drive(_middleware(dsh), _http_scope("/agent-workspace/"))
    assert status == 404
    assert dsh.ensure_calls == ["user1"]


def test_workspace_proxy_exchanges_token_on_every_root_navigation(respx_mock) -> None:
    """根导航始终由服务端换取 token→Cookie，旧 Cookie 不得让工作台卡在 401。

    DSH 会话 Cookie 由进程内密钥签名，runtime 重启（同端口）后浏览器手里的
    旧 Cookie 必然失效；据此跳过换取会永久 401。
    """
    target = "http://127.0.0.1:48400"
    authority = "127.0.0.1:48400"
    name = dsh_session_cookie_name(authority)
    dsh = _FakeDshService(target, auth=("/", "token=launch-token"))
    exchange = respx_mock.get(f"{target}/?token=launch-token").mock(
        return_value=httpx.Response(
            303,
            headers={
                "location": "/",
                "set-cookie": f"{name}=fresh; Path=/; HttpOnly; SameSite=Strict",
            },
        )
    )
    follow = respx_mock.get(f"{target}/").mock(return_value=httpx.Response(200, text="<html>app"))

    scope = _http_scope("/agent-workspace/", cookie=f"{name}=stale", origin="http://bridge.internal:8080")
    status, headers, body = _drive(_middleware(dsh), scope)

    assert exchange.called and follow.called
    # 浏览器只看到最终页面与新鲜 Cookie，不再经历 303 重定向
    assert status == 200
    assert body == b"<html>app"
    assert headers["set-cookie"] == f"{name}=fresh; Path=/; HttpOnly; SameSite=Strict"
    # 上游后续请求用新鲜 Cookie 覆盖旧值，并把 Origin 改写为目标源
    forwarded = follow.calls[-1].request.headers
    assert forwarded["cookie"] == f"{name}=fresh"
    assert forwarded["origin"] == target
    assert dsh.auth_calls == ["user1"]

    # 浏览器已带 token 或非根路径时不覆盖查询串
    dsh2 = _FakeDshService(target, auth=("/", "token=launch-token"))
    forwarded_route = respx_mock.get(f"{target}/app").mock(
        return_value=httpx.Response(200, text="ok")
    )
    _drive(_middleware(dsh2), _http_scope("/agent-workspace/app"))
    assert forwarded_route.called
    assert dsh2.auth_calls == []


def test_workspace_proxy_exchange_ignores_stale_dsh_session_cookies(respx_mock) -> None:
    """回归：真实 DSH 在 token 交换命中旧会话 Cookie 时只回 303、不下发 Set-Cookie。

    旧实现把浏览器 Cookie 原样带给交换请求，检测不到新 Cookie 后又把 303 转回
    浏览器，根导航便在 /agent-workspace/ 上无限重定向。交换请求必须不带任何
    浏览器 Cookie，浏览器最终直接拿到 200。
    """
    target = "http://127.0.0.1:48400"
    authority = "127.0.0.1:48400"
    name = dsh_session_cookie_name(authority)
    other_port = dsh_session_cookie_name("127.0.0.1:48399")
    dsh = _FakeDshService(target, auth=("/", "token=launch-token"))

    def exchange_behaviour(request: httpx.Request) -> httpx.Response:
        if "dsh-auth" in request.headers.get("cookie", ""):
            # 真实 DSH：命中旧会话 Cookie 时只回 303，不重新下发 Set-Cookie
            return httpx.Response(303, headers={"location": "/"})
        return httpx.Response(
            303,
            headers={
                "location": "/",
                "set-cookie": f"{name}=fresh; Path=/; HttpOnly; SameSite=Strict",
            },
        )

    exchange = respx_mock.get(f"{target}/?token=launch-token").mock(side_effect=exchange_behaviour)
    follow = respx_mock.get(f"{target}/").mock(return_value=httpx.Response(200, text="<html>app"))

    browser_cookies = "; ".join(
        [
            f"{name}=stale",
            f"{other_port}=older-port",
            "agent_bridge_admin=root-secret",
            "ph_phc_stats=1",
        ]
    )
    scope = _http_scope("/agent-workspace/", cookie=browser_cookies, origin="http://bridge.internal:8080")
    status, headers, body = _drive(_middleware(dsh), scope)

    assert exchange.called and follow.called
    # 交换请求不带任何浏览器 Cookie：旧 dsh-auth 会话与平台/统计 Cookie 都不进上游
    assert "cookie" not in exchange.calls[-1].request.headers
    # 浏览器直接拿到 200 与新鲜会话 Cookie，不再经历 303 重定向
    assert status == 200
    assert body == b"<html>app"
    assert headers["set-cookie"] == f"{name}=fresh; Path=/; HttpOnly; SameSite=Strict"
    # follow 请求只带 DSH 自己的 Cookie，会话替换为新鲜值
    forwarded = follow.calls[-1].request.headers
    assert forwarded["cookie"] == f"{name}=fresh"


def test_workspace_proxy_narrows_forwarded_cookies(respx_mock) -> None:
    """常规转发（含逃逸路径）只把 DSH 自己的 Cookie 发往上游。"""
    target = "http://127.0.0.1:48400"
    authority = "127.0.0.1:48400"
    name = dsh_session_cookie_name(authority)
    dsh = _FakeDshService(target)
    page = respx_mock.get(f"{target}/app").mock(return_value=httpx.Response(200, text="ok"))
    bare = respx_mock.get(f"{target}/app2").mock(return_value=httpx.Response(200, text="ok"))

    scope = _http_scope(
        "/agent-workspace/app",
        cookie=f"agent_bridge_admin=root-secret; {name}=sess; ph_phc_stats=1",
    )
    status, _, _ = _drive(_middleware(dsh), scope)
    assert status == 200 and page.called
    forwarded = page.calls[-1].request.headers
    assert forwarded["cookie"] == f"{name}=sess"
    assert "agent_bridge_admin" not in forwarded.get("cookie", "")

    # 收敛后没有可转发 Cookie 时应删除 Cookie 头，而不是发送空值
    status, _, _ = _drive(
        _middleware(dsh), _http_scope("/agent-workspace/app2", cookie="agent_bridge_admin=root-secret")
    )
    assert status == 200 and bare.called
    assert "cookie" not in bare.calls[-1].request.headers


def test_workspace_proxy_recovers_rotated_launch_token(respx_mock) -> None:
    """回归：DSH 的启动 token 随 Connection 重载静默轮换并重印横幅。

    状态里捕获的 token 过期后交换 401；代理必须重扫日志取最新 token 重试，
    浏览器仍直接拿到 200 与新鲜 Cookie。
    """
    target = "http://127.0.0.1:48400"
    authority = "127.0.0.1:48400"
    name = dsh_session_cookie_name(authority)
    dsh = _FakeDshService(
        target,
        auth=("/", "token=stale-token"),
        refreshed_auth=("/", "token=rotated-token"),
    )
    stale = respx_mock.get(f"{target}/?token=stale-token").mock(
        return_value=httpx.Response(401, text="dsh web authentication required")
    )
    rotated = respx_mock.get(f"{target}/?token=rotated-token").mock(
        return_value=httpx.Response(
            303,
            headers={
                "location": "/",
                "set-cookie": f"{name}=fresh; Path=/; HttpOnly; SameSite=Strict",
            },
        )
    )
    follow = respx_mock.get(f"{target}/").mock(return_value=httpx.Response(200, text="<html>app"))

    scope = _http_scope("/agent-workspace/", origin="http://bridge.internal:8080")
    status, headers, body = _drive(_middleware(dsh), scope)

    assert stale.called and rotated.called and follow.called
    assert dsh.refresh_calls == ["user1"]
    # 浏览器直接拿到 200 与新鲜 Cookie，token 轮换被完全吸收在服务端
    assert status == 200
    assert body == b"<html>app"
    assert headers["set-cookie"] == f"{name}=fresh; Path=/; HttpOnly; SameSite=Strict"
    assert follow.calls[-1].request.headers["cookie"] == f"{name}=fresh"


def test_workspace_proxy_falls_back_to_valid_session_without_token_replay(respx_mock) -> None:
    """token 失效但浏览器会话仍有效：直连代理返回 200，绝不回放带 token 的 303。

    真实 DSH 对“已认证 + token 查询”只回去掉查询串的 303 且不下发 Set-Cookie；
    旧实现把该响应回放给浏览器，导致 /agent-workspace/ 无限重定向。
    """
    target = "http://127.0.0.1:48400"
    authority = "127.0.0.1:48400"
    name = dsh_session_cookie_name(authority)
    dsh = _FakeDshService(target, auth=("/", "token=dead-token"))
    exchange = respx_mock.get(f"{target}/?token=dead-token").mock(
        return_value=httpx.Response(401, text="dsh web authentication required")
    )
    direct = respx_mock.get(f"{target}/").mock(return_value=httpx.Response(200, text="<html>app"))

    scope = _http_scope(
        "/agent-workspace/", cookie=f"{name}=valid-session", origin="http://bridge.internal:8080"
    )
    status, _, body = _drive(_middleware(dsh), scope)

    assert exchange.called and direct.called
    # 重扫无新横幅（返回同一入口）时不做第二次交换
    assert dsh.refresh_calls == ["user1"]
    # 浏览器直接 200：兜底请求不带 token 查询串，只带 DSH 自己的会话 Cookie
    assert status == 200
    assert body == b"<html>app"
    request = direct.calls[-1].request
    assert "token=" not in str(request.url)
    assert request.headers["cookie"] == f"{name}=valid-session"


def test_workspace_proxy_surfaces_401_when_exchange_and_session_both_dead(respx_mock) -> None:
    """token 与浏览器会话双双失效（如清了 Cookie）：如实回放 DSH 的 401，不循环。"""
    target = "http://127.0.0.1:48400"
    dsh = _FakeDshService(target, auth=("/", "token=expired"))
    respx_mock.get(f"{target}/?token=expired").mock(
        return_value=httpx.Response(401, text="dsh web authentication required")
    )
    direct = respx_mock.get(f"{target}/").mock(
        return_value=httpx.Response(401, text="dsh web authentication required")
    )
    status, _, body = _drive(_middleware(dsh), _http_scope("/agent-workspace/"))
    assert status == 401
    assert b"authentication required" in body
    # 401 来自不带 token 的直连代理，而不是对交换响应的回放
    assert direct.called
    assert "token=" not in str(direct.calls[-1].request.url)


def test_workspace_proxy_routes_escaped_root_requests(respx_mock) -> None:
    """DSH 前端以根绝对路径请求资源与 /api/**，按 Referer 归属工作台。"""
    target = "http://127.0.0.1:48400"
    dsh = _FakeDshService(target)
    asset = respx_mock.get(f"{target}/assets/index-BKQ.js").mock(
        return_value=httpx.Response(200, text="console.log(1)")
    )
    api = respx_mock.post(f"{target}/api/session/list").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    referer = "http://bridge.internal:8080/agent-workspace/"

    status, _, _ = _drive(
        _middleware(dsh), _http_scope("/assets/index-BKQ.js", referer=referer)
    )
    assert status == 200 and asset.called

    scope = _http_scope("/api/session/list", referer=referer)
    scope["method"] = "POST"
    status, _, body = _drive(_middleware(dsh), scope)
    assert status == 200 and api.called and json.loads(body) == {"ok": True}


def test_workspace_proxy_leaves_agent_bridge_paths_alone() -> None:
    """保留路径（平台接口/静态资源）不得被逃逸路由抢走。"""

    async def passthrough_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = AgentWorkspaceProxyMiddleware(
        app=passthrough_app,
        service=_FakeBridgeService(_FakeDshService("http://127.0.0.1:48400")),
        identity_resolver=_identity_resolver(),
    )
    referer = "http://bridge.internal:8080/agent-workspace/"
    assert _drive(middleware, _http_scope("/api/v1/dsh/runtime", referer=referer))[0] == 204
    assert _drive(middleware, _http_scope("/agent-bridge/assets/index.js", referer=referer))[0] == 204
    assert _drive(middleware, _http_scope("/assets/index.js"))[0] == 204


def test_workspace_proxy_forwards_and_rewrites_headers(respx_mock) -> None:
    dsh = _FakeDshService("http://127.0.0.1:48400")
    respx_mock.get("http://127.0.0.1:48400/app").mock(
        return_value=httpx.Response(
            302,
            headers={
                "location": "http://127.0.0.1:48400/login",
                "set-cookie": "session=abc; Path=/; HttpOnly",
            },
        )
    )
    status, headers, _ = _drive(_middleware(dsh), _http_scope("/agent-workspace/app"))
    assert status == 302
    assert headers["location"] == "/agent-workspace/login"
    # 非会话 Cookie 原样透传（Path 不再改写，避免根绝对路径请求丢失会话）
    assert headers["set-cookie"] == "session=abc; Path=/; HttpOnly"
    assert dsh.touch_calls == ["user1"]


def test_workspace_proxy_streams_sse_first_event(respx_mock) -> None:
    dsh = _FakeDshService("http://127.0.0.1:48400")
    middleware = _middleware(dsh)
    first_event = b'data: {"type":"connected"}\n\n'
    never_finish = asyncio.Event()

    async def upstream_stream():
        yield first_event
        await never_finish.wait()

    respx_mock.get("http://127.0.0.1:48400/events").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=upstream_stream(),
        )
    )

    sent: list[bytes] = []
    body_sent = False

    async def receive():
        nonlocal body_sent
        if not body_sent:
            body_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await never_finish.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body":
            sent.append(message.get("body", b""))

    scope = _http_scope("/agent-workspace/events")

    async def driver():
        task = asyncio.create_task(middleware(scope, receive, send))
        await asyncio.sleep(0.3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        never_finish.set()

    asyncio.run(driver())
    assert b"".join(sent).startswith(first_event)


# -- 反向代理：WebSocket（TestClient + 进程内 echo 服务器） --


class _EchoWebSocketServer:
    def __init__(self) -> None:
        self.port = 0
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stop = threading.Event()

    def start(self) -> None:
        from websockets.asyncio.server import serve

        async def _run() -> None:
            async def handler(connection) -> None:
                async for message in connection:
                    await connection.send(message)

            async with serve(handler, "127.0.0.1", 0) as server:
                self.port = server.sockets[0].getsockname()[1]
                self._ready.set()
                while not self._stop.is_set():
                    await asyncio.sleep(0.05)

        self._thread = threading.Thread(target=asyncio.run, args=(_run(),), daemon=True)
        self._thread.start()
        assert self._ready.wait(5), "echo websocket server did not start"

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)


def test_workspace_proxy_relays_websocket(service, home, passwd_lookup, monkeypatch) -> None:
    from agent_bridge.api.app import create_app

    server = _EchoWebSocketServer()
    server.start()
    try:
        configure_group(service)
        client = TestClient(create_app(service.paths, {"root"}))
        app_service = client.app.state.agent_bridge_service
        install_fakes(app_service, home, passwd_lookup, monkeypatch)
        # 直接登记指向 echo 服务器的 runtime 状态（pid 存活 + 端口健康）
        fake_pid = 424242
        monkeypatch.setattr(app_service.dsh, "_pid_alive", staticmethod(lambda pid: pid == fake_pid))
        monkeypatch.setattr(
            app_service.dsh, "_probe_port", staticmethod(lambda port: port == server.port)
        )
        app_service.dsh._write_state(
            "user1",
            {
                "user_id": "user1",
                "group_key": "groupa",
                "linux_user": "groupa",
                "pid": fake_pid,
                "port": server.port,
                "config_dir": "",
                "log_path": "",
                "profile_key": "safe",
                "started_at": time.time(),
                "last_access_at": time.time() - 600,
            },
        )

        with client.websocket_connect(
            "/agent-workspace/ws", headers={"X-Agent-Bridge-User": "user1"}
        ) as websocket:
            websocket.send_text("你好，workspace")
            assert websocket.receive_text() == "你好，workspace"
            websocket.send_bytes(b"\x01\x02")
            assert websocket.receive_bytes() == b"\x01\x02"

        refreshed = app_service.dsh._read_state("user1")
        assert float(refreshed["last_access_at"]) > time.time() - 60
    finally:
        server.stop()


def test_workspace_proxy_websocket_rejects_without_identity(service) -> None:
    from agent_bridge.api.app import create_app

    client = TestClient(create_app(service.paths, {"root"}))
    with pytest.raises(Exception):
        with client.websocket_connect("/agent-workspace/ws"):
            pass  # pragma: no cover - 未识别身份必须直接关闭
