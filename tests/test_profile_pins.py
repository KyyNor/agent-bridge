from __future__ import annotations

import pytest

from agent_bridge.capability_hub.governance import CapabilityGovernanceService
from agent_bridge.capability_hub.models import ProfileRuleEffect, SourceType, ToolType
from agent_bridge.core.config import AgentBridgePaths
from agent_bridge.core.domain import ValidationError
from agent_bridge.storage.sqlite import SQLiteStore


def _profile(store: SQLiteStore) -> None:
    store.upsert_project_profile(
        profile_key="safe-readonly",
        name="Safe Readonly",
        description="",
        status="active",
        created_by="root",
    )


def _service_with_tools(store: SQLiteStore, service_key: str = "mysql") -> None:
    store.create_mcp_service(
        service_key=service_key,
        name=service_key.upper(),
        endpoint_url=f"https://{service_key}.test/mcp",
        headers={},
        description="",
        tags=[],
        created_by="root",
    )
    store.update_mcp_service_status(service_key, "enabled")
    store.upsert_mcp_tool(
        service_key=service_key,
        tool_name="query_users",
        display_name="Query Users",
        description="Find users",
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
        tool_type=ToolType.search.value,
        tags=[],
        examples=[],
    )
    store.upsert_mcp_tool(
        service_key=service_key,
        tool_name="delete_user",
        display_name="Delete User",
        description="Delete users",
        input_schema={"type": "object", "properties": {"id": {"type": "string"}}},
        tool_type=ToolType.action.value,
        tags=[],
        examples=[],
    )


def test_profile_manual_pin_rules_round_trip(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    _profile(store)

    store.replace_profile_pin_rules(
        "safe-readonly",
        [
            {"service_key": "mysql", "tool_type": ToolType.search.value, "created_by": "root"},
            {"service_key": "jira", "tool_type": ToolType.detail.value, "created_by": "root"},
        ],
    )

    rows = store.list_profile_pin_rules("safe-readonly")
    assert [(row["service_key"], row["tool_type"]) for row in rows] == [
        ("jira", "detail"),
        ("mysql", "search"),
    ]


def test_profile_auto_pin_settings_round_trip(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    _profile(store)

    settings = store.upsert_profile_pin_settings(
        profile_key="safe-readonly",
        mode="ratio",
        ratio_percent=10,
        count=None,
        auto_cache=None,
    )

    assert settings["mode"] == "ratio"
    assert settings["ratio_percent"] == 10
    assert settings["count"] is None
    assert store.get_profile_pin_settings("safe-readonly")["mode"] == "ratio"


def test_governance_rejects_non_readonly_pin_type(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    _profile(store)
    _service_with_tools(store)
    governance = CapabilityGovernanceService(store=store, admins={"root"})

    with pytest.raises(ValidationError, match="tool_type is not pinnable"):
        governance.replace_profile_pins(
            "root",
            "safe-readonly",
            [{"service_key": "mysql", "tool_type": ToolType.action.value}],
        )


def test_compute_profile_pin_preview_filters_to_allowed_readonly_tools(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    _profile(store)
    _service_with_tools(store, "mysql")
    _service_with_tools(store, "hive")
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
    governance = CapabilityGovernanceService(store=store, admins={"root"})
    governance.replace_profile_pins(
        "root",
        "safe-readonly",
        [{"service_key": "mysql", "tool_type": ToolType.search.value}],
    )

    preview = governance.profile_pin_preview("root", "safe-readonly")

    assert [(group["service_key"], group["tool_type"], group["source"]) for group in preview["groups"]] == [
        ("mysql", "search", "manual")
    ]
    assert [tool["generated_tool_name"] for tool in preview["tools"]] == ["pin_mysql_query_users"]


def test_auto_pin_count_adds_highest_called_group_without_trimming_manual(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    _profile(store)
    _service_with_tools(store, "mysql")
    _service_with_tools(store, "jira")
    store.replace_profile_source_rules(
        "safe-readonly",
        [
            {"source_type": SourceType.mcp_service.value, "source_key": "mysql", "effect": "allow"},
            {"source_type": SourceType.mcp_service.value, "source_key": "jira", "effect": "allow"},
        ],
    )
    store.create_tool_call_log(
        log_id="call_jira_1",
        actor="root",
        profile_key="safe-readonly",
        entrypoint="metamcp_execute",
        source_type=SourceType.mcp_service.value,
        source_key="jira",
        tool_name="query_users",
        request={},
        response={},
        status="success",
    )
    governance = CapabilityGovernanceService(store=store, admins={"root"})
    governance.replace_profile_pins(
        "root",
        "safe-readonly",
        [{"service_key": "mysql", "tool_type": ToolType.search.value}],
    )
    governance.update_profile_pin_settings("root", "safe-readonly", mode="count", ratio_percent=None, count=2)

    preview = governance.profile_pin_preview("root", "safe-readonly")

    assert [(g["service_key"], g["tool_type"], g["source"]) for g in preview["groups"]] == [
        ("mysql", "search", "manual"),
        ("jira", "search", "auto"),
    ]
    assert store.get_profile_pin_settings("safe-readonly")["auto_cache_json"] is not None


def test_replace_profile_rules_clears_auto_pin_cache(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    _profile(store)
    _service_with_tools(store, "mysql")
    _service_with_tools(store, "jira")
    store.replace_profile_source_rules(
        "safe-readonly",
        [
            {"source_type": SourceType.mcp_service.value, "source_key": "mysql", "effect": "allow"},
            {"source_type": SourceType.mcp_service.value, "source_key": "jira", "effect": "allow"},
        ],
    )
    store.create_tool_call_log(
        log_id="call_jira_1",
        actor="root",
        profile_key="safe-readonly",
        entrypoint="metamcp_execute",
        source_type=SourceType.mcp_service.value,
        source_key="jira",
        tool_name="query_users",
        request={},
        response={},
        status="success",
    )
    governance = CapabilityGovernanceService(store=store, admins={"root"})
    governance.update_profile_pin_settings("root", "safe-readonly", mode="count", count=1)
    assert store.get_profile_pin_settings("safe-readonly")["auto_cache_json"] is not None

    governance.replace_profile_rules(
        "root",
        "safe-readonly",
        [
            {
                "source_type": SourceType.mcp_service.value,
                "source_key": "mysql",
                "effect": ProfileRuleEffect.allow.value,
            }
        ],
    )

    assert store.get_profile_pin_settings("safe-readonly")["auto_cache_json"] is None


def test_governance_accepts_profile_pin_setting_aliases(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    _profile(store)
    governance = CapabilityGovernanceService(store=store, admins={"root"})

    preview = governance.update_profile_pin_settings(
        "root",
        "safe-readonly",
        mode="count",
        count=2,
    )

    assert preview["settings"]["mode"] == "count"
    assert preview["settings"]["count"] == 2
    assert preview["settings"]["ratio_percent"] is None


def test_profile_pin_preview_rejects_generated_tool_name_collision(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    _profile(store)
    _service_with_tools(store, "foo-bar")
    _service_with_tools(store, "foo_bar")
    store.replace_profile_source_rules(
        "safe-readonly",
        [
            {
                "source_type": SourceType.mcp_service.value,
                "source_key": "foo-bar",
                "effect": ProfileRuleEffect.allow.value,
            },
            {
                "source_type": SourceType.mcp_service.value,
                "source_key": "foo_bar",
                "effect": ProfileRuleEffect.allow.value,
            },
        ],
    )
    store.replace_profile_pin_rules(
        "safe-readonly",
        [
            {"service_key": "foo-bar", "tool_type": ToolType.search.value, "created_by": "root"},
            {"service_key": "foo_bar", "tool_type": ToolType.search.value, "created_by": "root"},
        ],
    )
    governance = CapabilityGovernanceService(store=store, admins={"root"})

    with pytest.raises(ValidationError, match="pinned tool name collision: pin_foo_bar_query_users"):
        governance.profile_pin_preview("root", "safe-readonly")
