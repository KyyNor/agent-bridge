from __future__ import annotations

import asyncio

from agent_bridge.capability_hub.models import ProfileResourceType, ProfileRuleEffect, SourceType, ToolType
from agent_bridge.capability_hub.gateway.metamcp import _request_profile, create_mcp_server
from agent_bridge.app.service import AgentBridgeService
from agent_bridge.storage.sqlite import SQLiteStore


def _register_service(wm_paths, service_key: str, name: str) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.create_mcp_service(
        service_key=service_key,
        name=name,
        endpoint_url=f"https://{service_key}.test/mcp",
        headers={},
        description="",
        tags=[],
        created_by="root",
    )
    store.update_mcp_service_status(service_key, "enabled")


def _create_profile(wm_paths, *, denied_service: str = "hive") -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.upsert_project_profile(
        profile_key="safe-readonly",
        name="安全只读",
        description="",
        status="active",
        created_by="root",
    )
    store.replace_profile_source_rules(
        "safe-readonly",
        [
            {
                "source_type": SourceType.mcp_service.value,
                "source_key": denied_service,
                "effect": ProfileRuleEffect.deny.value,
            }
        ],
    )


def test_mcp_search_lists_registered_services(wm_paths) -> None:
    _register_service(wm_paths, "mysql", "MySQL")
    _register_service(wm_paths, "hive", "Hive")

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    mcp = create_mcp_server(svc)
    _, structured = asyncio.run(mcp.call_tool("search", {}))
    assert [item["service"] for item in structured["items"]] == [
        "built-in",
        "wiki",
        "codegraph",
        "memory",
        "hive",
        "mysql",
    ]
    assert structured["log_id"].startswith("call_")


def test_mcp_search_lists_external_and_builtin_sources_with_profile(wm_paths) -> None:
    _register_service(wm_paths, "mysql", "MySQL")
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.create_kb("frontend-docs", "Frontend Docs", "", "root")
    store.upsert_project_profile(
        profile_key="safe-readonly",
        name="安全只读",
        description="",
        status="active",
        created_by="root",
    )
    store.replace_profile_resource_rules(
        "safe-readonly",
        [{"resource_type": ProfileResourceType.wiki_kb.value, "resource_key": "frontend-docs"}],
    )
    store.replace_profile_source_rules(
        "safe-readonly",
        [{"source_type": "mcp_service", "source_key": "mysql", "effect": "allow"}],
    )

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    mcp = create_mcp_server(svc)
    token = _request_profile.set("safe-readonly")
    try:
        _, structured = asyncio.run(mcp.call_tool("search", {}))
    finally:
        _request_profile.reset(token)

    services = [item["service"] for item in structured["items"]]
    wiki = next(item for item in structured["items"] if item["service"] == "wiki")
    assert "mysql" in services
    assert "wiki" in services
    assert "codegraph" in services
    assert wiki["resources"] == [
        {"resource_type": "wiki_kb", "resource_key": "frontend-docs", "name": "Frontend Docs"}
    ]


def test_mcp_search_filters_by_query(wm_paths) -> None:
    _register_service(wm_paths, "mysql", "MySQL")
    _register_service(wm_paths, "hive", "Hive")

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    mcp = create_mcp_server(svc)
    _, structured = asyncio.run(mcp.call_tool("search", {"query": "my"}))
    assert [item["service"] for item in structured["items"]] == ["mysql"]


def test_mcp_exposes_profile_pinned_tools(wm_paths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.create_mcp_service(
        service_key="mysql",
        name="MySQL",
        endpoint_url="https://mysql.test/mcp",
        headers={},
        description="",
        tags=[],
        created_by="root",
    )
    store.update_mcp_service_status("mysql", "enabled")
    store.upsert_mcp_tool(
        service_key="mysql",
        tool_name="query_users",
        display_name="Query Users",
        description="Find users",
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
        tool_type=ToolType.search.value,
        tags=[],
        examples=[],
    )
    store.upsert_project_profile(
        profile_key="safe-readonly",
        name="Safe Readonly",
        description="",
        status="active",
        created_by="root",
    )
    store.replace_profile_source_rules(
        "safe-readonly",
        [
            {
                "source_type": SourceType.mcp_service.value,
                "source_key": "mysql",
                "effect": ProfileRuleEffect.allow.value,
            }
        ],
    )
    store.replace_profile_pin_rules(
        "safe-readonly",
        [{"service_key": "mysql", "tool_type": ToolType.search.value, "created_by": "root"}],
    )

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    mcp = create_mcp_server(svc, profile_key="safe-readonly")
    token = _request_profile.set("safe-readonly")
    try:
        tools = asyncio.run(mcp.list_tools())
    finally:
        _request_profile.reset(token)

    pinned_tool = next(tool for tool in tools if tool.name == "pin_mysql_query_users")
    assert pinned_tool.inputSchema["properties"]["q"]["type"] == "string"
