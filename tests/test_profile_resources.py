from __future__ import annotations

import pytest

from agent_bridge.capabilities.governance import CapabilityGovernanceService
from agent_bridge.core.config import AgentBridgePaths
from agent_bridge.core.domain import NotFound, ValidationError
from agent_bridge.storage.sqlite import SQLiteStore


def _service(wm_paths: AgentBridgePaths) -> CapabilityGovernanceService:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    service = CapabilityGovernanceService(store=store, admins={"root"})
    service.upsert_profile("root", "safe-readonly", "安全只读", "", "active")
    return service


def test_profile_resource_rules_round_trip_and_filter(wm_paths: AgentBridgePaths) -> None:
    service = _service(wm_paths)

    detail = service.replace_profile_resource_rules(
        "root",
        "safe-readonly",
        [
            {"resource_type": "wiki_kb", "resource_key": "frontend-docs"},
            {"resource_type": "code_repo", "resource_key": "web-app"},
        ],
    )

    assert [(rule["resource_type"], rule["resource_key"]) for rule in detail["resource_rules"]] == [
        ("code_repo", "web-app"),
        ("wiki_kb", "frontend-docs"),
    ]
    assert service.filter_resource_keys(
        actor="root",
        profile_key="safe-readonly",
        resource_type="wiki_kb",
        resource_keys=["frontend-docs", "payroll"],
    ) == ["frontend-docs"]
    assert service.is_resource_allowed("root", "safe-readonly", "code_repo", "web-app") is True
    assert service.is_resource_allowed("root", "safe-readonly", "code_repo", "payroll") is False


def test_profile_resource_rules_default_open_without_profile_and_closed_with_profile(
    wm_paths: AgentBridgePaths,
) -> None:
    service = _service(wm_paths)

    assert service.filter_resource_keys(
        actor="root",
        profile_key=None,
        resource_type="wiki_kb",
        resource_keys=["frontend-docs", "payroll"],
    ) == ["frontend-docs", "payroll"]
    assert service.filter_resource_keys(
        actor="root",
        profile_key="safe-readonly",
        resource_type="wiki_kb",
        resource_keys=["frontend-docs", "payroll"],
    ) == []


def test_profile_resource_rules_validate_type_and_profile(wm_paths: AgentBridgePaths) -> None:
    service = _service(wm_paths)

    with pytest.raises(ValidationError, match="invalid resource type"):
        service.replace_profile_resource_rules(
            "root",
            "safe-readonly",
            [{"resource_type": "wrong", "resource_key": "x"}],
        )
    with pytest.raises(NotFound, match="profile not found"):
        service.replace_profile_resource_rules("root", "missing", [])
