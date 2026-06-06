from __future__ import annotations

import json

import pytest

from agent_bridge.capabilities.models import CallLogStatus, SourceType
from agent_bridge.capabilities.governance import CapabilityGovernanceService
from agent_bridge.core.config import AgentBridgePaths
from agent_bridge.core.domain import AccessDenied, NotFound, ValidationError
from agent_bridge.storage.sqlite import SQLiteStore


def _service(wm_paths: AgentBridgePaths) -> tuple[CapabilityGovernanceService, SQLiteStore]:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    return CapabilityGovernanceService(store=store, admins={"root"}), store


def test_profile_crud_requires_admin_and_lists_rules(wm_paths: AgentBridgePaths) -> None:
    service, _store = _service(wm_paths)

    with pytest.raises(AccessDenied):
        service.upsert_profile("alice", "safe-readonly", "安全只读", "", "active")

    profile = service.upsert_profile("root", "safe-readonly", "安全只读", "只读项目", "active")
    service.replace_profile_rules(
        "root",
        "safe-readonly",
        [
            {"source_type": "mcp_service", "source_key": "mysql", "effect": "allow"},
            {"source_type": "mcp_service", "source_key": "hive", "effect": "deny"},
        ],
    )

    assert profile["profile_key"] == "safe-readonly"
    detail = service.get_profile("root", "safe-readonly")
    assert detail["name"] == "安全只读"
    assert [(rule["source_key"], rule["effect"]) for rule in detail["rules"]] == [
        ("hive", "deny"),
        ("mysql", "allow"),
    ]
    assert service.list_profiles("root")[0]["allow_count"] == 1


def test_profile_reads_require_admin(wm_paths: AgentBridgePaths) -> None:
    service, _store = _service(wm_paths)
    service.upsert_profile("root", "safe-readonly", "安全只读", "", "active")
    log = service.log_tool_call(
        actor="alice",
        profile_key=None,
        entrypoint="metamcp_search",
        source_type=None,
        source_key=None,
        tool_name="search",
        request={},
        response={},
        status=CallLogStatus.success.value,
        error_message=None,
        duration_ms=1,
    )

    with pytest.raises(AccessDenied):
        service.list_profiles("alice")
    with pytest.raises(AccessDenied):
        service.get_profile("alice", "safe-readonly")
    with pytest.raises(AccessDenied):
        service.replace_profile_rules("alice", "safe-readonly", [])
    with pytest.raises(AccessDenied):
        service.list_logs(actor="alice")
    with pytest.raises(AccessDenied):
        service.get_log(actor="alice", log_id=log["log_id"])


def test_policy_filters_sources_with_allow_and_deny(wm_paths: AgentBridgePaths) -> None:
    service, _store = _service(wm_paths)
    service.upsert_profile("root", "safe-readonly", "安全只读", "", "active")
    service.replace_profile_rules(
        "root",
        "safe-readonly",
        [
            {"source_type": "mcp_service", "source_key": "mysql", "effect": "allow"},
            {"source_type": "mcp_service", "source_key": "hive", "effect": "deny"},
        ],
    )

    visible = service.filter_source_keys(
        actor="root",
        profile_key="safe-readonly",
        source_type=SourceType.mcp_service.value,
        source_keys=["mysql", "hive", "wiki"],
    )

    assert visible == ["mysql"]
    assert service.is_source_allowed("root", "safe-readonly", "mcp_service", "mysql") is True
    assert service.is_source_allowed("root", "safe-readonly", "mcp_service", "hive") is False
    assert service.is_source_allowed("root", None, "mcp_service", "hive") is True


def test_policy_defaults_allow_when_allow_rules_are_empty_and_denies_first(wm_paths: AgentBridgePaths) -> None:
    service, _store = _service(wm_paths)
    service.upsert_profile("root", "deny-only", "默认允许", "", "active")
    service.replace_profile_rules(
        "root",
        "deny-only",
        [{"source_type": "mcp_service", "source_key": "hive", "effect": "deny"}],
    )

    visible = service.filter_source_keys(
        actor="alice",
        profile_key="deny-only",
        source_type=SourceType.mcp_service.value,
        source_keys=["mysql", "hive", "wiki"],
    )

    assert visible == ["mysql", "wiki"]


def test_policy_rules_match_source_type_and_source_key_pair(wm_paths: AgentBridgePaths) -> None:
    service, store = _service(wm_paths)
    service.upsert_profile("root", "safe-readonly", "安全只读", "", "active")
    store.replace_profile_source_rules(
        "safe-readonly",
        [
            {"source_type": "openapi_service", "source_key": "mysql", "effect": "deny"},
            {"source_type": SourceType.mcp_service.value, "source_key": "mysql", "effect": "allow"},
        ],
    )

    visible = service.filter_source_keys(
        actor="root",
        profile_key="safe-readonly",
        source_type=SourceType.mcp_service.value,
        source_keys=["mysql", "wiki"],
    )

    assert visible == ["mysql"]


def test_policy_rejects_invalid_source_type_for_checks(wm_paths: AgentBridgePaths) -> None:
    service, _store = _service(wm_paths)
    service.upsert_profile("root", "safe-readonly", "安全只读", "", "active")
    service.replace_profile_rules(
        "root",
        "safe-readonly",
        [{"source_type": "mcp_service", "source_key": "mysql", "effect": "deny"}],
    )

    with pytest.raises(ValidationError, match="invalid source type"):
        service.filter_source_keys(
            actor="root",
            profile_key="safe-readonly",
            source_type="typo",
            source_keys=["mysql"],
        )

    with pytest.raises(ValidationError, match="invalid source type"):
        service.is_source_allowed("root", "safe-readonly", "typo", "mysql")


def test_unknown_or_disabled_profile_is_not_found(wm_paths: AgentBridgePaths) -> None:
    service, _store = _service(wm_paths)
    service.upsert_profile("root", "disabled", "停用", "", "disabled")

    with pytest.raises(NotFound, match="profile not found"):
        service.filter_source_keys(
            actor="root",
            profile_key="missing",
            source_type="mcp_service",
            source_keys=["mysql"],
        )

    with pytest.raises(ValidationError, match="profile is disabled"):
        service.filter_source_keys(
            actor="root",
            profile_key="disabled",
            source_type="mcp_service",
            source_keys=["mysql"],
        )


def test_write_and_read_tool_call_log_payloads(wm_paths: AgentBridgePaths) -> None:
    service, _store = _service(wm_paths)

    log = service.log_tool_call(
        actor="root",
        profile_key="safe-readonly",
        entrypoint="metamcp_search",
        source_type=None,
        source_key=None,
        tool_name="search",
        request={"query": "mysql"},
        response={"items": []},
        status=CallLogStatus.success.value,
        error_message=None,
        duration_ms=3,
    )

    assert log["log_id"].startswith("call_")
    listed = service.list_logs(actor="root", profile_key="safe-readonly")
    assert listed[0]["log_id"] == log["log_id"]
    detail = service.get_log(actor="root", log_id=log["log_id"])
    assert json.loads(detail["request_json"]) == {"query": "mysql"}
    assert json.loads(detail["response_json"]) == {"items": []}
    assert detail["status"] == "success"
    assert detail["duration_ms"] == 3


def test_missing_log_is_not_found(wm_paths: AgentBridgePaths) -> None:
    service, _store = _service(wm_paths)

    with pytest.raises(NotFound, match="tool call log not found"):
        service.get_log(actor="root", log_id="call_missing")


def test_log_filters_and_writes_reject_invalid_source_type_and_status(wm_paths: AgentBridgePaths) -> None:
    service, _store = _service(wm_paths)

    with pytest.raises(ValidationError, match="invalid source type"):
        service.log_tool_call(
            actor="root",
            profile_key=None,
            entrypoint="metamcp_execute",
            source_type="typo",
            source_key="mysql",
            tool_name="query_sql",
            request={},
            response={},
            status=CallLogStatus.success.value,
            error_message=None,
            duration_ms=1,
        )

    with pytest.raises(ValidationError, match="invalid call log status"):
        service.log_tool_call(
            actor="root",
            profile_key=None,
            entrypoint="metamcp_execute",
            source_type=SourceType.mcp_service.value,
            source_key="mysql",
            tool_name="query_sql",
            request={},
            response={},
            status="maybe",
            error_message=None,
            duration_ms=1,
        )

    with pytest.raises(ValidationError, match="invalid source type"):
        service.list_logs(actor="root", source_type="typo")

    with pytest.raises(ValidationError, match="invalid call log status"):
        service.list_logs(actor="root", status="maybe")


def test_log_filters_validate_failure_fields(wm_paths: AgentBridgePaths) -> None:
    service, _store = _service(wm_paths)

    with pytest.raises(ValidationError, match="invalid failure stage"):
        service.list_logs(actor="root", failure_stage="wrong")

    with pytest.raises(ValidationError, match="invalid failure owner"):
        service.list_logs(actor="root", failure_owner="wrong")

    with pytest.raises(ValidationError, match="invalid failure stage"):
        service.log_tool_call(
            actor="root",
            profile_key=None,
            entrypoint="metamcp_search",
            source_type=None,
            source_key=None,
            tool_name="search",
            request={},
            response={},
            status=CallLogStatus.error.value,
            error_message="bad",
            duration_ms=1,
            failure_stage="wrong",
            failure_owner=None,
            error_type="internal_error",
        )

    with pytest.raises(ValidationError, match="invalid failure owner"):
        service.log_tool_call(
            actor="root",
            profile_key=None,
            entrypoint="metamcp_search",
            source_type=None,
            source_key=None,
            tool_name="search",
            request={},
            response={},
            status=CallLogStatus.error.value,
            error_message="bad",
            duration_ms=1,
            failure_stage="internal",
            failure_owner="wrong",
            error_type="internal_error",
        )


def test_rule_validation_rejects_unknown_effect_source_type_and_empty_key(wm_paths: AgentBridgePaths) -> None:
    service, _store = _service(wm_paths)
    service.upsert_profile("root", "safe-readonly", "安全只读", "", "active")

    with pytest.raises(ValidationError, match="invalid rule effect"):
        service.replace_profile_rules(
            "root",
            "safe-readonly",
            [{"source_type": "mcp_service", "source_key": "mysql", "effect": "maybe"}],
        )

    with pytest.raises(ValidationError, match="invalid source type"):
        service.replace_profile_rules(
            "root",
            "safe-readonly",
            [{"source_type": "unknown", "source_key": "mysql", "effect": "allow"}],
        )

    with pytest.raises(ValidationError, match="source_key is required"):
        service.replace_profile_rules(
            "root",
            "safe-readonly",
            [{"source_type": "mcp_service", "source_key": " ", "effect": "allow"}],
        )


def test_profile_validation_rejects_bad_status_and_missing_profile_rules(wm_paths: AgentBridgePaths) -> None:
    service, _store = _service(wm_paths)

    with pytest.raises(ValidationError, match="invalid profile status"):
        service.upsert_profile("root", "safe-readonly", "安全只读", "", "archived")

    with pytest.raises(NotFound, match="profile not found"):
        service.replace_profile_rules("root", "missing", [])
