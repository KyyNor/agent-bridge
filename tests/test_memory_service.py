from __future__ import annotations

from agent_bridge.app.service import AgentBridgeService
from agent_bridge.core.domain import ValidationError


class FakeWorkerService:
    def search(self, block, *, query, limit):
        return {
            "status": "ok",
            "block_key": block["block_key"],
            "items": [{"id": "obs-1", "summary": query, "content_preview": "hit", "score": 1.0}],
        }

    def timeline(self, block, *, limit, cursor):
        return {
            "status": "ok",
            "block_key": block["block_key"],
            "items": [{"id": "obs-1", "event_type": "tool", "summary": "Read"}],
            "next_cursor": cursor,
        }

    def get_observation(self, block, observation_id):
        return {
            "status": "ok",
            "block_key": block["block_key"],
            "item": {"id": observation_id, "content": "Observation"},
        }

    def health(self, block):
        return {"status": "worker_ready", "base_url": "http://127.0.0.1:8766"}


def _service(wm_paths):
    service = AgentBridgeService.create(wm_paths, admins={"root"})
    service.init_system()
    return service


def test_create_memory_block_uses_server_side_default_data_dir(wm_paths):
    service = _service(wm_paths)

    block = service.memory.create_block(
        actor="root",
        block_key="dev-memory",
        name="Dev Memory",
        description="Project memory",
    )

    assert block["block_key"] == "dev-memory"
    assert block["data_dir"] == str(wm_paths.data_dir / "claude-mem" / "blocks" / "dev-memory")


def test_profile_binding_requires_existing_profile_and_active_block(wm_paths):
    service = _service(wm_paths)
    service.governance.upsert_profile("root", "dev", "Dev", "", "active")
    service.memory.create_block("root", "dev-memory", "Dev Memory", "")

    binding = service.memory.set_profile_binding("root", "dev", "dev-memory", enabled=True)

    assert binding["profile_key"] == "dev"
    assert binding["block_key"] == "dev-memory"


def test_profile_binding_rejects_disabled_block(wm_paths):
    service = _service(wm_paths)
    service.governance.upsert_profile("root", "dev", "Dev", "", "active")
    service.memory.create_block("root", "dev-memory", "Dev Memory", "")
    service.memory.set_block_status("root", "dev-memory", "disabled")

    try:
        service.memory.set_profile_binding("root", "dev", "dev-memory", enabled=True)
    except ValidationError as exc:
        assert "memory block is not active" in exc.message
    else:
        raise AssertionError("expected ValidationError")


def test_resolve_profile_block_returns_not_configured_when_unbound(wm_paths):
    service = _service(wm_paths)
    service.governance.upsert_profile("root", "dev", "Dev", "", "active")

    resolved = service.memory.resolve_profile_block("root", "dev")

    assert resolved["status"] == "not_configured"
    assert resolved["block"] is None


def test_memory_search_returns_not_configured_for_unbound_profile(wm_paths):
    service = _service(wm_paths)
    service.governance.upsert_profile("root", "dev", "Dev", "", "active")

    result = service.memory.search(actor="root", profile_key="dev", query="deploy", limit=5)

    assert result == {"status": "not_configured", "block_key": None, "items": []}


def test_memory_runtime_methods_delegate_to_worker_for_active_binding(wm_paths):
    service = _service(wm_paths)
    service.governance.upsert_profile("root", "dev", "Dev", "", "active")
    service.memory.create_block("root", "dev-memory", "Dev Memory", "")
    service.memory.set_profile_binding("root", "dev", "dev-memory", enabled=True)
    service.memory.worker_service = FakeWorkerService()

    search = service.memory.search(actor="root", profile_key="dev", query="deploy", limit=5)
    timeline = service.memory.timeline(actor="root", profile_key="dev", limit=5)
    observation = service.memory.get_observation(actor="root", profile_key="dev", observation_id="obs-1")
    health = service.memory.block_health("root", "dev-memory")

    assert search["block_key"] == "dev-memory"
    assert search["items"][0]["summary"] == "deploy"
    assert timeline["items"][0]["event_type"] == "tool"
    assert observation["item"]["content"] == "Observation"
    assert health["status"] == "worker_ready"
