"""工作台 API 路由的泛化与别名兼容测试（issue #17）。

``/api/v1/agent-workspace/*`` 是统一入口（带 ``agent`` 参数），
``/api/v1/dsh/*`` 作为 DSH 别名保留，至少一个版本内语义等价。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(wm_paths):
    from agent_bridge.app.service import AgentBridgeService

    service = AgentBridgeService.create(wm_paths, {"root"})
    service.access.upsert_group(actor="root", group_key="groupa", name="A 组")
    service.access.create_user(actor="root", user_id="user1")
    service.access.set_user_group(actor="root", user_id="user1", group_key="groupa")
    from agent_bridge.api.app import create_app

    return TestClient(create_app(wm_paths, {"root"}))


def test_agents_catalog_lists_both(client) -> None:
    response = client.get("/api/v1/agent-workspace/agents")
    assert response.status_code == 200
    agents = {item["agent"]: item for item in response.json()["agents"]}
    assert set(agents) == {"dsh", "pi"}
    assert agents["pi"]["workspace_url"] == "/agent-workspace-pi/"
    assert agents["pi"]["shared_workspace_url"] == "/agent-workspace-pi-shared/"
    assert agents["dsh"]["workspace_url"] == "/agent-workspace/"


def test_dsh_alias_matches_generic_route(client) -> None:
    """旧 ``/api/v1/dsh/*`` 与新 ``?agent=dsh`` 返回等价结果。"""
    headers = {"X-Agent-Bridge-User": "user1"}
    legacy = client.get("/api/v1/dsh/runtime?scope=personal", headers=headers)
    generic = client.get(
        "/api/v1/agent-workspace/runtime?agent=dsh&scope=personal", headers=headers
    )
    assert legacy.status_code == generic.status_code == 200
    legacy_body = legacy.json()
    generic_body = generic.json()
    assert legacy_body["scope"] == generic_body["scope"]
    assert legacy_body["status"] == generic_body["status"]
    assert generic_body["agent"] == "dsh"


def test_pi_runtime_route_distinct_from_dsh(client) -> None:
    headers = {"X-Agent-Bridge-User": "user1"}
    dsh = client.get("/api/v1/agent-workspace/runtime?agent=dsh", headers=headers).json()
    pi = client.get("/api/v1/agent-workspace/runtime?agent=pi", headers=headers).json()
    assert dsh["agent"] == "dsh"
    assert pi["agent"] == "pi"


def test_unknown_agent_is_rejected(client) -> None:
    headers = {"X-Agent-Bridge-User": "user1"}
    response = client.get("/api/v1/agent-workspace/runtime?agent=bogus", headers=headers)
    assert response.status_code >= 400


def test_pi_versions_endpoint(client) -> None:
    response = client.get(
        "/api/v1/agent-workspace/pi-versions", headers={"X-Agent-Bridge-User": "user1"}
    )
    assert response.status_code == 200
    payload = response.json()
    assert any(item.startswith("@agegr/pi-web@") for item in payload["packages"])
    assert "{port}" in payload["default_web_command"]


def test_workspace_authorize_accepts_agent_param(client) -> None:
    """未配置组时返回 4xx 而非 5xx：泛化路由行为与 DSH 一致。"""
    response = client.post(
        "/api/v1/agent-workspace/workspace/authorize?agent=pi",
        headers={"X-Agent-Bridge-User": "user1"},
        json={"profile_key": None, "scope": "personal"},
    )
    assert response.status_code in {200, 400, 403}


def test_runtimes_admin_only(client) -> None:
    """未登录时管理接口必须拒绝。"""
    assert client.get("/api/v1/agent-workspace/runtimes").status_code in {401, 403}
    assert client.get("/api/v1/dsh/runtimes").status_code in {401, 403}

def test_runtime_config_persists_pi_web_command(client) -> None:
    """Pi 启动命令必须真正落库（历史上 Pydantic 会静默丢弃未知字段）。"""
    headers = {"X-Agent-Bridge-User": "root"}  # 运行配置是管理员接口
    current = client.get(
        "/api/v1/agent-workspace/runtime-config", headers=headers
    ).json()

    saved = client.put(
        "/api/v1/agent-workspace/runtime-config",
        headers=headers,
        json={
            "web_command": current["web_command"],
            "pi_web_command": "pi-web --no-open --port {port} --hostname 127.0.0.1",
            "idle_timeout_minutes": current["idle_timeout_minutes"],
            "base_url": current["base_url"],
            "available_models": current["available_models"],
            "expected_edit_token": current.get("edit_token"),
        },
    )
    assert saved.status_code == 200, saved.text
    assert (
        saved.json()["pi_web_command"]
        == "pi-web --no-open --port {port} --hostname 127.0.0.1"
    )
    # 回读确认已持久化
    reread = client.get(
        "/api/v1/agent-workspace/runtime-config", headers=headers
    ).json()
    assert reread["pi_web_command"] == saved.json()["pi_web_command"]
