"""DSH Workspace 真实进程 smoke test。

以 tests/dsh_test_workspace_server.py 作为 DSH Web 替身（HTTP 鉴权 + WebSocket
同端口），覆盖「选平面 → 授权 → 启动 → 代理（含首访 token 换取）→ MCP 注入」
的完整链路。仅在 ``./scripts/test.sh all``（``-m process``）时执行。
"""

from __future__ import annotations

import getpass
import os
import sys
import time
import types
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from agent_bridge.dsh.workspace import mcp_overlay_path

pytestmark = pytest.mark.process

_SERVER_SCRIPT = Path(__file__).parent / "dsh_test_workspace_server.py"


@pytest.fixture
def client(wm_paths, tmp_path):
    from agent_bridge.api.app import create_app
    from agent_bridge.app.service import AgentBridgeService

    service = AgentBridgeService.create(wm_paths, {"root"})
    service.access.upsert_group(actor="root", group_key="groupa", name="A 组")
    service.access.create_user(actor="root", user_id="user1")
    service.access.set_user_group(actor="root", user_id="user1", group_key="groupa")
    service.governance.upsert_profile("user1", "safe", "安全平面", "", "active")
    linux_home = tmp_path / "linux-home"
    linux_home.mkdir()
    current_user = getpass.getuser()
    service.dsh._passwd_lookup = lambda user: types.SimpleNamespace(
        pw_uid=os.getuid(), pw_gid=os.getgid(), pw_dir=str(linux_home / user)
    )
    service.dsh_configs.save_runtime_config(
        "root",
        web_command=f'"{sys.executable}" "{_SERVER_SCRIPT}" {{patch}} --host 127.0.0.1 --port {{port}}',
        idle_timeout_minutes=120,
        base_url="http://model.internal/v1",
        available_models=["gpt-x", "gpt-y"],
    )
    service.dsh_configs.save_group_config(
        "root",
        group_key="groupa",
        linux_user=current_user,
        default_model="gpt-x",
        api_key="sk-secret",
    )
    app = create_app(service.paths, {"root"})
    # create_app 会装配新的 service 实例：进程级 fakes 必须装在 app 上，
    # 否则真实 passwd 会把 DSH 配置写进开发机真实 home。
    app_service = app.state.agent_bridge_service
    app_service.dsh._passwd_lookup = lambda user: types.SimpleNamespace(
        pw_uid=os.getuid(), pw_gid=os.getgid(), pw_dir=str(linux_home / user)
    )
    yield TestClient(app), linux_home / current_user / ".config" / "dsh" / "user1"
    app_service.dsh.stop_all()


def test_workspace_full_flow_with_real_process(client) -> None:
    client, config_dir = client
    headers_user = {"X-Agent-Bridge-User": "user1"}

    # 未识别身份：代理直接拒绝
    assert client.get("/agent-workspace/").status_code == 401

    # 选择能力平面并进入工作台（真实子进程启动）
    authorized = client.post(
        "/api/v1/dsh/workspace/authorize", headers=headers_user, json={"profile_key": "safe"}
    )
    assert authorized.status_code == 200, authorized.text
    payload = authorized.json()
    assert payload["status"] == "running"
    assert payload["profile_key"] == "safe"

    # DSH 原生 settings.yaml 与 MCP 覆盖文件都落在用户 DSH_HOME 内
    settings = yaml.safe_load((config_dir / "settings.yaml").read_text(encoding="utf-8"))
    provider = settings["llm-pi-ai"]["providers"]["agent-bridge"]
    assert provider["baseURL"] == "http://model.internal/v1"
    assert provider["apiKeyEnv"] == "AGENT_BRIDGE_DSH_API_KEY"
    assert [model["id"] for model in provider["models"]] == ["gpt-x", "gpt-y"]
    assert settings["agent-default-model"]["model"] == "gpt-x"

    overlay_path = mcp_overlay_path(config_dir)
    overlay = yaml.safe_load(overlay_path.read_text(encoding="utf-8"))
    mcp_config = overlay[0]["insert"][0]["config"]
    assert mcp_config["url"].endswith("/mcp")
    assert mcp_config["headers"]["X-Agent-Bridge-MetaMCP-Profile"] == "safe"
    assert mcp_config["headers"]["X-Agent-Bridge-DSH-Capability"]

    # 首访：代理用服务端捕获的 token 完成 303 + Cookie，浏览器地址保持前缀
    first_visit = client.get("/agent-workspace/", headers=headers_user, follow_redirects=False)
    assert first_visit.status_code == 303
    assert first_visit.headers["location"] == "/agent-workspace/"
    cookie = first_visit.headers.get("set-cookie", "")
    assert "Path=/agent-workspace/" in cookie
    session_cookie = cookie.split(";", 1)[0]

    # 携带会话 Cookie 后正常渲染（Cookie 由浏览器管理，测试显式透传）
    page = client.get(
        "/agent-workspace/",
        headers={**headers_user, "Cookie": session_cookie},
    )
    assert page.status_code == 200
    assert page.text == "dsh-standin"

    # 子进程确实收到 --patch 覆盖文件（能力平面注入生效）
    patched = client.get(
        "/agent-workspace/patch", headers={**headers_user, "Cookie": session_cookie}
    )
    assert patched.status_code == 200
    assert patched.text == str(overlay_path)

    # Location 与 Set-Cookie 改写
    redirected = client.get(
        "/agent-workspace/redirect", headers={**headers_user, "Cookie": session_cookie}, follow_redirects=False
    )
    assert redirected.headers["location"] == "/agent-workspace/login"
    cookie_response = client.get(
        "/agent-workspace/set-cookie", headers={**headers_user, "Cookie": session_cookie}
    )
    assert "Path=/agent-workspace/" in cookie_response.headers.get("set-cookie", "")

    # WebSocket 代理：echo 往返
    with client.websocket_connect(
        "/agent-workspace/ws", headers={**headers_user, "Cookie": session_cookie}
    ) as websocket:
        websocket.send_text("hello-dsh")
        assert websocket.receive_text() == "hello-dsh"

    # 代理命中刷新空闲时间
    status = client.get("/api/v1/dsh/runtime", headers=headers_user).json()
    assert status["status"] == "running"
    assert status["profile_key"] == "safe"
    assert status["idle_minutes"] is not None and status["idle_minutes"] < 5

    # 无权限 Profile 切换被拒绝
    denied = client.post(
        "/api/v1/dsh/workspace/authorize", headers=headers_user, json={"profile_key": "nope"}
    )
    assert denied.status_code == 404

    # 不带能力平面重新进入：覆盖文件被清除
    plain = client.post("/api/v1/dsh/workspace/authorize", headers=headers_user, json={})
    assert plain.status_code == 200
    assert plain.json()["profile_key"] is None
    assert not overlay_path.exists()

    # 停止后无小组归属的用户访问被拒绝（403 而非 503）
    stopped = client.post("/api/v1/dsh/runtime/stop", headers=headers_user)
    assert stopped.json()["stopped"] is True
    time.sleep(0.2)
    assert client.get("/agent-workspace/", headers={"X-Agent-Bridge-User": "user2"}).status_code == 403
