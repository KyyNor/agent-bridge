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
    match_workspace_path,
    rewrite_workspace_location,
    rewrite_workspace_set_cookie,
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


def test_rewrite_workspace_set_cookie_path() -> None:
    assert (
        rewrite_workspace_set_cookie("session=abc; Path=/; HttpOnly")
        == "session=abc; Path=/agent-workspace/; HttpOnly"
    )
    assert (
        rewrite_workspace_set_cookie("session=abc; path=/dsh; Secure")
        == "session=abc; path=/agent-workspace/dsh; Secure"
    )
    assert (
        rewrite_workspace_set_cookie("session=abc; Path=/agent-workspace/sub")
        == "session=abc; Path=/agent-workspace/sub"
    )


# -- 反向代理：HTTP（直接驱动 middleware + respx） --


class _FakeDshService:
    def __init__(self, target: str | None, *, auth: tuple[str, str] | None = None) -> None:
        self._target = target
        self._auth = auth
        self.ensure_calls: list[str] = []
        self.touch_calls: list[str] = []
        self.auth_calls: list[str] = []

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


class _FakeBridgeService:
    def __init__(self, dsh: _FakeDshService) -> None:
        self.dsh = dsh


def _http_scope(path: str, user: str | None = "user1", query: str = "") -> dict:
    headers = []
    if user is not None:
        headers.append((b"x-agent-bridge-user", user.encode("utf-8")))
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


def test_workspace_proxy_injects_dsh_first_visit_token(respx_mock) -> None:
    """首访必须带上 DSH 启动 token：服务端完成 token→Cookie 换取。"""
    dsh = _FakeDshService("http://127.0.0.1:48400", auth=("/", "token=launch-token"))
    route = respx_mock.get("http://127.0.0.1:48400/?token=launch-token").mock(
        return_value=httpx.Response(303, headers={"location": "/"})
    )
    status, headers, _ = _drive(_middleware(dsh), _http_scope("/agent-workspace/"))
    assert route.called
    assert status == 303
    assert headers["location"] == "/agent-workspace/"
    assert dsh.auth_calls == ["user1"]

    # 浏览器已带 token 或非根路径时不覆盖查询串
    dsh2 = _FakeDshService("http://127.0.0.1:48400", auth=("/", "token=launch-token"))
    forwarded = respx_mock.get("http://127.0.0.1:48400/app").mock(
        return_value=httpx.Response(200, text="ok")
    )
    _drive(_middleware(dsh2), _http_scope("/agent-workspace/app"))
    assert forwarded.called
    assert dsh2.auth_calls == []


def test_workspace_proxy_skips_token_when_session_cookie_present(respx_mock) -> None:
    """已持有 DSH 会话 Cookie 时不再注入 token，避免 303 重定向循环。"""
    from agent_bridge.api.workspace_proxy import _dsh_session_cookie_name

    target = "http://127.0.0.1:48400"
    cookie = f"{_dsh_session_cookie_name('127.0.0.1:48400')}=v1.abc.def"
    dsh = _FakeDshService(target, auth=("/", "token=launch-token"))
    route = respx_mock.get("http://127.0.0.1:48400/").mock(return_value=httpx.Response(200, text="ok"))
    scope = _http_scope("/agent-workspace/")
    scope["headers"].append((b"cookie", cookie.encode("utf-8")))
    status, _, body = _drive(_middleware(dsh), scope)
    assert route.called
    assert status == 200
    assert body == b"ok"
    assert dsh.auth_calls == []


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
    assert "Path=/agent-workspace/" in headers["set-cookie"]
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
