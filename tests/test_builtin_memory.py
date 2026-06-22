from __future__ import annotations

import asyncio

from agent_bridge.app.service import AgentBridgeService


def test_memory_builtin_returns_not_configured_for_unbound_profile(wm_paths):
    service = AgentBridgeService.create(wm_paths, admins={"root"})
    service.init_system()
    service.governance.upsert_profile("root", "dev", "Dev", "", "active")
    provider = service.capabilities.builtin_providers["memory"]

    result = asyncio.run(
        provider.execute("root", "search", {"query": "deploy", "limit": 5}, profile_key="dev")
    )

    assert result == {"status": "not_configured", "block_key": None, "items": []}
