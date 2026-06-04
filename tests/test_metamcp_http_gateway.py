from __future__ import annotations

import asyncio

from wiki_manager.capabilities import ProfileRuleEffect, SourceType
from wiki_manager.mcp_server import create_mcp_server
from wiki_manager.services import WikiManagerService
from wiki_manager.storage import SQLiteStore


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

    svc = WikiManagerService.create(wm_paths, {"root"})
    svc.store.init_schema()
    mcp = create_mcp_server(svc)
    _, structured = asyncio.run(mcp.call_tool("search", {}))
    assert [item["service"] for item in structured["items"]] == ["hive", "mysql"]
    assert structured["log_id"].startswith("call_")


def test_mcp_search_filters_by_query(wm_paths) -> None:
    _register_service(wm_paths, "mysql", "MySQL")
    _register_service(wm_paths, "hive", "Hive")

    svc = WikiManagerService.create(wm_paths, {"root"})
    svc.store.init_schema()
    mcp = create_mcp_server(svc)
    _, structured = asyncio.run(mcp.call_tool("search", {"query": "my"}))
    assert [item["service"] for item in structured["items"]] == ["mysql"]
