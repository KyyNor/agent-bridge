from __future__ import annotations

import json

from wiki_manager.capabilities import CallLogStatus, PolicyContext, ProfileRuleEffect, SourceRef, SourceType
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

    detail = store.get_tool_call_log("call_20260603_153012_ab12")
    assert detail is not None
    assert json.loads(detail["request_json"]) == request


def test_tool_call_log_allows_missing_profile(wm_paths: WikiManagerPaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()

    log = store.create_tool_call_log(
        log_id="call_without_profile",
        actor="root",
        profile_key=None,
        entrypoint="metamcp_search",
        request={"path": None, "query": None},
        response={"items": []},
        status=CallLogStatus.success,
    )

    assert log["profile_key"] is None
    assert store.list_tool_call_logs(profile_key="missing") == []


def test_policy_context_defaults_are_profile_optional() -> None:
    mysql_source = SourceRef(SourceType.mcp_service.value, "mysql")
    context = PolicyContext(actor="root", allow_sources={mysql_source})

    assert context.profile_key is None
    assert context.allow_sources == {mysql_source}
    assert context.deny_sources is None
    assert context.entrypoint == "metamcp_search"


def test_tool_call_log_migration_allows_missing_profile(wm_paths: WikiManagerPaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    with store.connect() as conn:
        conn.executescript(
            """
            CREATE TABLE tool_call_logs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              log_id TEXT NOT NULL UNIQUE,
              actor TEXT NOT NULL,
              profile_key TEXT NOT NULL,
              entrypoint TEXT NOT NULL,
              source_type TEXT,
              source_key TEXT,
              tool_name TEXT,
              request_json TEXT NOT NULL DEFAULT '{}',
              response_json TEXT NOT NULL DEFAULT '{}',
              status TEXT NOT NULL,
              error_message TEXT,
              duration_ms INTEGER,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX idx_tool_call_logs_created_at ON tool_call_logs(created_at DESC, id DESC);
            CREATE INDEX idx_tool_call_logs_profile ON tool_call_logs(profile_key);
            CREATE INDEX idx_tool_call_logs_source ON tool_call_logs(source_type, source_key);
            INSERT INTO tool_call_logs (
              log_id,
              actor,
              profile_key,
              entrypoint,
              status
            )
            VALUES ('old_call', 'root', 'safe-readonly', 'metamcp_search', 'success');
            """
        )

    store.init_schema()

    migrated = store.get_tool_call_log("old_call")
    assert migrated is not None
    assert migrated["profile_key"] == "safe-readonly"

    new_log = store.create_tool_call_log(
        log_id="new_call_without_profile",
        actor="root",
        profile_key=None,
        entrypoint="metamcp_search",
        status=CallLogStatus.success,
    )
    assert new_log["profile_key"] is None
