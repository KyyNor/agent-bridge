from __future__ import annotations

from agent_bridge.core.domain import BackendCapabilities
from agent_bridge.knowledge_management.docs_knowledge.service import DocsKnowledgeService


class _AgentCapableBackend:
    def __init__(self) -> None:
        self.maintenance_calls = 0

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            supports_folders=False,
            supports_agents=True,
            supports_managed_resources=True,
        )

    def list_agents(self):
        return [
            {
                "id": "custom-agent",
                "name": "自定义 Agent",
                "config": {"agent_type": "custom"},
            }
        ]

    def get_type_presets(self):
        return [
            {
                "id": "custom",
                "config": {"agent_type": "custom"},
                "i18n": {"zh-CN": {"description": "自定义类型"}},
            }
        ]

    def create_agent(self, name, preset_config):
        return {"id": "created", "name": name, "config": preset_config["config"]}

    def ensure_managed_resources(self):
        self.maintenance_calls += 1
        return {"ok": True}


class _Registry:
    def __init__(self, adapter) -> None:
        self.adapter = adapter

    def get(self, slug):
        return self.adapter if slug == "custom" else None

    def list_slugs(self):
        return ["custom"]


def test_agent_management_uses_capability_protocol_not_backend_type() -> None:
    backend = _AgentCapableBackend()
    registry = _Registry(backend)
    service = DocsKnowledgeService(
        store=object(),
        admins={"root"},
        registry_provider=lambda: registry,
    )

    assert service.list_backend_agents("root", "custom") == [
        {
            "agent_id": "custom-agent",
            "name": "自定义 Agent",
            "agent_type": "custom",
            "is_builtin": False,
        }
    ]
    created = service.create_backend_agent("root", "custom", "新 Agent", "custom")
    assert created["agent_id"] == "created"

    service.ensure_managed_resources()
    assert backend.maintenance_calls == 1
