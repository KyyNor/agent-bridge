from __future__ import annotations

from fastapi.testclient import TestClient


def test_agent_runtime_config_api_updates_registry(wm_paths) -> None:
    from agent_bridge.api.app import create_app

    app = create_app(wm_paths, {"root"})
    client = TestClient(app)
    headers = {"X-Agent-Bridge-User": "root"}

    initial = client.get("/agent-runtime/config", headers=headers)
    assert initial.status_code == 200
    assert initial.json()["default_backend"] == "claude"
    assert [item["slug"] for item in initial.json()["available_backends"]] == ["claude"]
    assert initial.json()["available_backends"][0]["capabilities"]["supports_mcp"] is True

    payload = {
        "default_backend": "opencode",
        "backends": [
            {
                "slug": "opencode",
                "type": "opencode",
                "command": "opencode",
                "model": "anthropic/claude-sonnet-4",
            }
        ],
    }
    saved = client.post("/agent-runtime/config", json=payload, headers=headers)

    assert saved.status_code == 200
    assert {key: saved.json()[key] for key in ("default_backend", "backends")} == payload
    assert [item["slug"] for item in saved.json()["available_backends"]] == ["claude", "opencode"]
    assert saved.json()["available_backends"][1]["capabilities"]["supports_mcp"] is True
    service = app.state.agent_bridge_service
    assert service.agents.coding_agents.default_backend == "opencode"
    assert "opencode" in service.agents.coding_agents.keys()


def test_agent_runtime_config_api_rejects_missing_default_backend(wm_paths) -> None:
    from agent_bridge.api.app import create_app

    app = create_app(wm_paths, {"root"})
    client = TestClient(app)

    response = client.post(
        "/agent-runtime/config",
        json={"default_backend": "opencode", "backends": []},
        headers={"X-Agent-Bridge-User": "root"},
    )

    assert response.status_code == 400


def test_agent_runtime_config_api_accepts_fixed_three_agent_backends(wm_paths) -> None:
    from agent_bridge.api.app import create_app

    app = create_app(wm_paths, {"root"})
    client = TestClient(app)

    payload = {
        "default_backend": "codex",
        "backends": [
            {"slug": "claude", "type": "claude", "command": None, "model": None},
            {"slug": "opencode", "type": "opencode", "command": "opencode", "model": None},
            {"slug": "codex", "type": "codex", "command": "codex", "model": "gpt-5"},
        ],
    }
    saved = client.post(
        "/agent-runtime/config",
        json=payload,
        headers={"X-Agent-Bridge-User": "root"},
    )

    assert saved.status_code == 200
    assert {key: saved.json()[key] for key in ("default_backend", "backends")} == payload
    assert [item["slug"] for item in saved.json()["available_backends"]] == ["claude", "codex", "opencode"]
    service = app.state.agent_bridge_service
    assert service.agents.coding_agents.default_backend == "codex"
    assert service.agents.coding_agents.keys() == ["claude", "codex", "opencode"]
