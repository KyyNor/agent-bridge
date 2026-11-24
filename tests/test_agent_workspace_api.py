"""工作台 API 路由的泛化与别名兼容测试（issue #17）。

``/api/v1/agent-workspace/*`` 是统一入口（带 ``agent`` 参数），
``/api/v1/dsh/*`` 作为 DSH 别名保留，至少一个版本内语义等价。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

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


def test_agents_catalog_requires_login(client) -> None:
    """Agent 目录必须鉴权：未识别调用者返回 401。"""
    assert client.get("/api/v1/agent-workspace/agents").status_code == 401


def test_agents_catalog_lists_both(client) -> None:
    response = client.get(
        "/api/v1/agent-workspace/agents", headers={"X-Agent-Bridge-User": "user1"}
    )
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


def test_runtime_config_keeps_pi_command_when_field_absent(client) -> None:
    """旧客户端不传 pi_web_command 时，必须保留已保存值而不是重置为默认。"""
    headers = {"X-Agent-Bridge-User": "root"}
    custom = "pi-web --no-open --port {port} --hostname 127.0.0.0.1"
    current = client.get(
        "/api/v1/agent-workspace/runtime-config", headers=headers
    ).json()
    saved = client.put(
        "/api/v1/agent-workspace/runtime-config",
        headers=headers,
        json={
            "web_command": current["web_command"],
            "pi_web_command": custom,
            "idle_timeout_minutes": current["idle_timeout_minutes"],
            "base_url": current["base_url"],
            "available_models": current["available_models"],
            "expected_edit_token": current.get("edit_token"),
        },
    )
    assert saved.status_code == 200
    assert saved.json()["pi_web_command"] == custom

    # 旧客户端：完全不传该字段
    current = client.get(
        "/api/v1/agent-workspace/runtime-config", headers=headers
    ).json()
    legacy = client.put(
        "/api/v1/agent-workspace/runtime-config",
        headers=headers,
        json={
            "web_command": current["web_command"],
            "idle_timeout_minutes": current["idle_timeout_minutes"],
            "base_url": current["base_url"],
            "available_models": current["available_models"],
            "expected_edit_token": current.get("edit_token"),
        },
    )
    assert legacy.status_code == 200
    assert legacy.json()["pi_web_command"] == custom, "旧客户端保存配置不得清空 Pi 启动命令"


def test_runtime_config_round_trips_model_vision_and_legacy_edits(client) -> None:
    path = "/api/v1/agent-workspace/runtime-config"
    headers = {"X-Agent-Bridge-User": "root"}
    initial = client.get(path, headers=headers).json()
    assert initial["model_vision"] == {}
    payload = {"idle_timeout_minutes": 120, "available_models": ["vision", "text", "legacy"]}
    seeded = client.put(path, headers=headers, json={**payload, "expected_edit_token": initial["edit_token"]})
    assert seeded.status_code == 200, seeded.text
    assert seeded.json()["model_vision"] == {"vision": False, "text": False, "legacy": False}
    saved = client.put(path, headers=headers, json={
        **payload, "model_vision": {"vision": True, "text": False}, "expected_edit_token": seeded.json()["edit_token"],
    })
    assert saved.status_code == 200, saved.text
    config = client.get("/api/v1/dsh/runtime-config", headers=headers).json()
    assert config["model_vision"] == {"vision": True, "text": False}

    updated_payload = {**payload, "available_models": ["vision", "legacy", "gpt-4o"]}
    legacy = client.put("/api/v1/dsh/runtime-config", headers=headers, json={
        **updated_payload, "expected_edit_token": config["edit_token"],
    })
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()["model_vision"] == {"vision": True, "gpt-4o": False}

    for vision_map, status in [({"unknown": True}, 400), ({"vision": "true"}, 422)]:
        rejected = client.put(path, headers=headers, json={
            **updated_payload, "model_vision": vision_map, "expected_edit_token": legacy.json()["edit_token"],
        })
        assert rejected.status_code == status, rejected.text
    assert client.get(path, headers=headers).json()["model_vision"] == {"vision": True, "gpt-4o": False}

    cleared = client.put(path, headers=headers, json={
        **updated_payload, "model_vision": {}, "expected_edit_token": legacy.json()["edit_token"],
    })
    assert cleared.status_code == 200, cleared.text
    assert client.get(path, headers=headers).json()["model_vision"] == {}


def test_runtime_config_rejects_competing_model_vision_edits(client) -> None:
    path = "/api/v1/agent-workspace/runtime-config"
    headers = {"X-Agent-Bridge-User": "root"}
    payload = {"idle_timeout_minutes": 120, "available_models": ["model", "other"]}
    assert client.put(path, headers=headers, json=payload).status_code == 200
    token = client.get(path, headers=headers).json()["edit_token"]

    def save(model_id: str):
        return client.put(path, headers=headers, json={
            **payload, "model_vision": {"model": model_id == "model", "other": model_id == "other"},
            "expected_edit_token": token,
        })

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(save, ["model", "other"]))
    assert sorted(response.status_code for response in results) == [200, 409]
    winner = next(response.json() for response in results if response.status_code == 200)
    assert client.get(path, headers=headers).json()["model_vision"] == winner["model_vision"]
