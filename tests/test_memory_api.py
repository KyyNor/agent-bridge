from __future__ import annotations

from fastapi.testclient import TestClient

from agent_bridge.api.app import create_app


class FakeContextWorkerService:
    def handle_hook(self, block, *, action, payload, event_name, matcher, timeout_seconds):
        return {
            "stdout": (
                '{"hookSpecificOutput":{"hookEventName":"SessionStart",'
                '"additionalContext":"Memory context from claude-mem."}}'
            ),
            "stderr": "",
            "exit_code": 0,
            "status": "ok",
        }


def _client(wm_paths):
    app = create_app(paths=wm_paths, admins={"root"})
    return TestClient(app)


def test_memory_block_api_crud_and_binding(wm_paths):
    client = _client(wm_paths)
    headers = {"X-Agent-Bridge-User": "root"}
    client.post(
        "/capability-profiles",
        json={"profile_key": "dev", "name": "Dev", "description": "", "status": "active"},
        headers=headers,
    )

    created = client.post(
        "/memory/blocks",
        json={"block_key": "dev-memory", "name": "Dev Memory", "description": "Project memory"},
        headers=headers,
    )
    assert created.status_code == 200
    assert created.json()["block_key"] == "dev-memory"
    assert created.json()["last_health"] == {}

    binding = client.put(
        "/capability-profiles/dev/memory",
        json={"block_key": "dev-memory", "enabled": True},
        headers=headers,
    )
    assert binding.status_code == 200
    assert binding.json()["block_key"] == "dev-memory"

    read_binding = client.get("/capability-profiles/dev/memory", headers=headers)
    assert read_binding.json()["block_key"] == "dev-memory"


def test_memory_hook_api_returns_noop_when_unbound(wm_paths):
    client = _client(wm_paths)
    headers = {"X-Agent-Bridge-User": "root"}
    client.post(
        "/capability-profiles",
        json={"profile_key": "dev", "name": "Dev", "description": "", "status": "active"},
        headers=headers,
    )

    response = client.post(
        "/memory/hooks/claude-code/context",
        json={
            "profile_key": "dev",
            "event_name": "SessionStart",
            "matcher": "startup|clear|compact",
            "payload": {"source": "startup"},
            "hook_timeout_seconds": 60,
            "source": "claude-code",
        },
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "not_configured"
    assert response.json()["exit_code"] == 0


def test_profile_doc_context_file_api_refreshes_memory_context(wm_paths):
    app = create_app(paths=wm_paths, admins={"root"})
    app.state.agent_bridge_service.memory.worker_service = FakeContextWorkerService()
    app.state.agent_bridge_service.memory.hooks.worker_service = FakeContextWorkerService()
    client = TestClient(app)
    headers = {"X-Agent-Bridge-User": "root"}
    client.post(
        "/capability-profiles",
        json={"profile_key": "dev", "name": "Dev", "description": "", "status": "active"},
        headers=headers,
    )
    client.post(
        "/memory/blocks",
        json={"block_key": "dev-memory", "name": "Dev Memory", "description": "Project memory"},
        headers=headers,
    )
    client.put(
        "/capability-profiles/dev/memory",
        json={"block_key": "dev-memory", "enabled": True},
        headers=headers,
    )

    response = client.post("/capability-profiles/dev/doc/context-file", headers=headers)

    profile_path = wm_paths.profiles_dir / "dev.md"
    assert response.status_code == 200
    assert response.json()["profile_doc_path"] == str(profile_path)
    assert "Memory context from claude-mem." in profile_path.read_text(encoding="utf-8")


def test_memory_dashboard_api_starts_worker_and_returns_embedded_url(wm_paths):
    app = create_app(paths=wm_paths, admins={"root"})
    app.state.agent_bridge_service.memory.worker_service.start_dashboard = lambda block: {  # type: ignore[attr-defined]
        "success": True,
        "running": True,
        "url": "http://127.0.0.1:48100/",
        "pid": 4242,
    }
    client = TestClient(app)
    headers = {"X-Agent-Bridge-User": "root"}
    client.post(
        "/memory/blocks",
        json={"block_key": "dev-memory", "name": "Dev Memory", "description": "Project memory"},
        headers=headers,
    )

    response = client.post("/memory/blocks/dev-memory/dashboard/start", headers=headers)

    assert response.status_code == 200
    assert response.json()["running"] is True
    assert response.json()["url"] == "/memory-dashboard/dev-memory/"
