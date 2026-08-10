"""TDD tests for capability service (MCP / OpenAPI service) deletion.

These cover the hard-delete + governance soft-rule cleanup contract agreed in the plan:
- admin can delete an MCP/OpenAPI service -> row + tools gone (FK CASCADE)
- non-admin is denied
- deleting a missing service raises NotFound
- governance soft-links (profile_source_rules / profile_pin_rules) are purged
"""
from __future__ import annotations

import asyncio

import pytest

from agent_bridge.capability_hub.models import ToolType
from agent_bridge.capability_hub.service import CapabilityService
from agent_bridge.core.config import AgentBridgePaths
from agent_bridge.core.domain import AccessDenied, NotFound
from agent_bridge.storage.sqlite import SQLiteStore

from tests.test_capability_service import FakeMcpClient


def _service(wm_paths: AgentBridgePaths) -> CapabilityService:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    return CapabilityService(store=store, mcp_client=FakeMcpClient(), admins={"root"})


def _seed_profile_with_mcp_rules(service: CapabilityService, source_key: str) -> None:
    """Create a profile referencing the given MCP service in source + pin rules.

    Seeded directly via the repository so pin-rule creation does not require the
    service to still exist after deletion.
    """
    service.governance.upsert_profile("root", "safe-readonly", "安全只读", "", "active")
    service.governance.replace_profile_rules(
        "root",
        "safe-readonly",
        [{"source_type": "mcp_service", "source_key": source_key, "effect": "allow"}],
    )
    service.store.replace_profile_pin_rules(
        "safe-readonly",
        [{"service_key": source_key, "tool_type": ToolType.search.value, "created_by": "root"}],
    )


# ---------------------------------------------------------------------------
# MCP service deletion
# ---------------------------------------------------------------------------


def test_delete_mcp_service_removes_service_and_tools(wm_paths: AgentBridgePaths) -> None:
    service = _service(wm_paths)
    service.register_service(
        "root", "docs-api", "Docs API", "https://example.test/mcp", {}, "docs service", ["docs"]
    )
    asyncio.run(service.sync_tools("root", "docs-api"))
    assert len(service.list_tools("alice", "docs-api")) == 2

    service.delete_mcp_service("root", "docs-api")

    assert service.store.get_mcp_service("docs-api") is None
    with service.store.connect() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM mcp_tools WHERE service_key = ?", ("docs-api",)
        ).fetchone()["c"]
    assert n == 0


def test_delete_mcp_service_requires_admin(wm_paths: AgentBridgePaths) -> None:
    service = _service(wm_paths)
    service.register_service("root", "docs-api", "Docs API", "https://example.test/mcp", {}, "", [])
    with pytest.raises(AccessDenied):
        service.delete_mcp_service("alice", "docs-api")


def test_delete_mcp_service_missing_raises_not_found(wm_paths: AgentBridgePaths) -> None:
    service = _service(wm_paths)
    with pytest.raises(NotFound, match="资源不存在"):
        service.delete_mcp_service("root", "missing")


def test_delete_mcp_service_cleans_governance_soft_rules(wm_paths: AgentBridgePaths) -> None:
    service = _service(wm_paths)
    service.register_service("root", "docs-api", "Docs API", "https://example.test/mcp", {}, "", [])
    _seed_profile_with_mcp_rules(service, "docs-api")

    # sanity: rules exist before delete
    assert any(
        r["source_key"] == "docs-api"
        for r in service.store.list_profile_source_rules("safe-readonly")
    )
    assert any(
        r["service_key"] == "docs-api"
        for r in service.store.list_profile_pin_rules("safe-readonly")
    )

    service.delete_mcp_service("root", "docs-api")

    assert not any(
        r["source_key"] == "docs-api"
        for r in service.store.list_profile_source_rules("safe-readonly")
    )
    assert not any(
        r["service_key"] == "docs-api"
        for r in service.store.list_profile_pin_rules("safe-readonly")
    )
    # profile itself remains
    assert service.store.get_project_profile("safe-readonly") is not None


# ---------------------------------------------------------------------------
# OpenAPI service deletion
# ---------------------------------------------------------------------------


def test_delete_openapi_service_removes_service_and_tools(wm_paths: AgentBridgePaths) -> None:
    service = _service(wm_paths)
    service.register_openapi_service(
        "root",
        "petstore",
        "Petstore",
        base_url="https://example.test",
        spec_url="https://example.test/openapi.json",
        spec_content="",
        auth_config={},
        headers={},
        description="",
        tags=[],
    )
    service.store.upsert_openapi_tool(
        service_key="petstore",
        tool_name="list_pets",
        operation_id="listPets",
        method="GET",
        path="/pets",
        display_name="List pets",
        description="",
        input_schema={"type": "object"},
        request_mapping={},
        response_schema={},
        tool_type=ToolType.overview.value,
        tags=[],
        examples=[],
    )
    assert service.store.get_openapi_service("petstore") is not None

    service.delete_openapi_service("root", "petstore")

    assert service.store.get_openapi_service("petstore") is None
    with service.store.connect() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM openapi_tools WHERE service_key = ?", ("petstore",)
        ).fetchone()["c"]
    assert n == 0


def test_delete_openapi_service_requires_admin(wm_paths: AgentBridgePaths) -> None:
    service = _service(wm_paths)
    service.register_openapi_service(
        "root", "petstore", "Petstore", base_url="https://example.test",
        spec_url="", spec_content="", auth_config={}, headers={}, description="", tags=[],
    )
    with pytest.raises(AccessDenied):
        service.delete_openapi_service("alice", "petstore")


def test_delete_openapi_service_missing_raises_not_found(wm_paths: AgentBridgePaths) -> None:
    service = _service(wm_paths)
    with pytest.raises(NotFound, match="资源不存在"):
        service.delete_openapi_service("root", "missing")
