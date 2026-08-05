from __future__ import annotations

import asyncio

import pytest

from agent_bridge.app.service import AgentBridgeService
from agent_bridge.capability_hub.gateway.metamcp import create_mcp_server
from agent_bridge.core.domain import NotFound


def _create_ledger(service: AgentBridgeService, key: str, name: str) -> None:
    service.business_ledgers.create_ledger(
        "root",
        ledger_key=key,
        name=name,
        description="",
        fields=[{"field_key": "name", "name": "名称", "field_type": "text", "query_modes": ["contains"]}],
    )
    service.business_ledgers.add_record("root", key, {"name": name})


def test_business_ledger_is_profile_scoped_and_exposed_as_top_level_tool(wm_paths) -> None:
    service = AgentBridgeService.create(wm_paths, {"root"})
    _create_ledger(service, "assets", "资产台账")
    _create_ledger(service, "secrets", "密钥台账")
    service.governance.upsert_profile("root", "safe", "安全平面", "", "active")
    service.governance.set_resource_profiles("root", "business_ledger", "assets", ["safe"])

    mcp = create_mcp_server(service, profile_key="safe")
    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
    assert "query_business_ledger" in tools
    assert set(tools["query_business_ledger"].inputSchema["properties"]) == {"ledger_key", "filters", "keyword", "sort", "limit", "offset"}

    _, result = asyncio.run(mcp.call_tool("query_business_ledger", {"ledger_key": "assets", "keyword": "资产"}))
    assert result["success"] is True
    assert result["result"]["total"] == 1

    with pytest.raises(Exception, match=r"当前可用业务台账：资产台账 \(assets\)"):
        asyncio.run(mcp.call_tool("query_business_ledger", {"ledger_key": "secrets"}))


def test_business_ledger_is_not_available_without_profile(wm_paths) -> None:
    service = AgentBridgeService.create(wm_paths, {"root"})
    _create_ledger(service, "assets", "资产台账")

    mcp = create_mcp_server(service)
    assert "query_business_ledger" not in {tool.name for tool in asyncio.run(mcp.list_tools())}
    with pytest.raises(NotFound):
        asyncio.run(service.capabilities.execute("root", "business_ledger", "query", {"ledger_key": "assets"}))
