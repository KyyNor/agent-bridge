from __future__ import annotations

import json

from wiki_manager.capabilities import CallLogStatus, ProfileRuleEffect, SourceType
from wiki_manager.config import WikiManagerPaths
from wiki_manager.storage import SQLiteStore


def test_project_profile_and_rules_round_trip(wm_paths: WikiManagerPaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()

    profile = store.upsert_project_profile(
        profile_key="safe-readonly",
        name="安全只读",
        description="只允许项目访问安全的查询服务。",
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
            },
            {
                "source_type": SourceType.mcp_service.value,
                "source_key": "hive",
                "effect": ProfileRuleEffect.deny.value,
            },
        ],
    )

    assert profile["profile_key"] == "safe-readonly"
    assert profile["name"] == "安全只读"
    assert profile["description"] == "只允许项目访问安全的查询服务。"
    assert store.get_project_profile("safe-readonly")["status"] == "active"

    profiles = store.list_project_profiles()
    assert profiles[0]["profile_key"] == "safe-readonly"
    assert profiles[0]["allow_count"] == 1
    assert profiles[0]["deny_count"] == 1

    rules = store.list_profile_source_rules("safe-readonly")
    assert [(rule["source_key"], rule["effect"]) for rule in rules] == [("hive", "deny"), ("mysql", "allow")]


def test_tool_call_log_round_trip_and_filters(wm_paths: WikiManagerPaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()

    request = {"query": "select * from users", "limit": 10}
    response = {"rows": [{"id": 1, "name": "张三"}], "truncated": False}
    log = store.create_tool_call_log(
        log_id="call_20260603_153012_ab12",
        actor="root",
        profile_key="safe-readonly",
        entrypoint="metamcp_execute",
        source_type=SourceType.mcp_service.value,
        source_key="mysql",
        tool_name="query_sql",
        request=request,
        response=response,
        status=CallLogStatus.success.value,
        duration_ms=42,
    )

    assert log["log_id"] == "call_20260603_153012_ab12"
    assert json.loads(log["request_json"]) == request
    assert json.loads(log["response_json"]) == response

    filtered = store.list_tool_call_logs(profile_key="safe-readonly", status="success")
    assert [item["log_id"] for item in filtered] == ["call_20260603_153012_ab12"]
    assert json.loads(filtered[0]["request_json"]) == request
    assert json.loads(filtered[0]["response_json"]) == response
