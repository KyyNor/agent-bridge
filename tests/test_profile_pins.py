from __future__ import annotations

from agent_bridge.capabilities.models import ProfileRuleEffect, SourceType, ToolType
from agent_bridge.core.config import AgentBridgePaths
from agent_bridge.storage.sqlite import SQLiteStore


def _profile(store: SQLiteStore) -> None:
    store.upsert_project_profile(
        profile_key="safe-readonly",
        name="Safe Readonly",
        description="",
        status="active",
        created_by="root",
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
