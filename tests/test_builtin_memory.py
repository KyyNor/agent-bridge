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


def test_memory_builtin_tool_schemas_include_chinese_descriptions(wm_paths):
    service = AgentBridgeService.create(wm_paths, admins={"root"})
    service.init_system()
    provider = service.capabilities.builtin_providers["memory"]

    tools = {tool.tool: tool for tool in provider.list_tools("root", None)}

    assert tools["search"].description == "检索当前 profile 绑定的记忆区块。"
    assert tools["search"].input_schema["properties"]["query"]["description"] == "要检索的记忆关键词或问题。"
    assert tools["timeline"].input_schema["properties"]["limit"]["description"] == "本次读取的时间线条目数量上限。"
    assert tools["get"].input_schema["properties"]["id"]["description"] == "要读取的记忆 observation ID。"
