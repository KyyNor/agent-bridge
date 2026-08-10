from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from agent_bridge.access_control.resources import create_scoped_resource_registry
from agent_bridge.access_control.service import AccessControlService
from agent_bridge.api.app import create_app
from agent_bridge.capability_hub.service import CapabilityService
from agent_bridge.core.domain import AccessDenied
from agent_bridge.storage.sqlite import SQLiteStore


def _configure_groups(client: TestClient) -> None:
    root = {"X-Agent-Bridge-User": "root"}
    for group_key in ("team-a", "team-b"):
        response = client.post(
            "/access/groups",
            headers=root,
            json={"group_key": group_key, "name": group_key.upper()},
        )
        assert response.status_code == 200
    for user_id, group_key in (("alice", "team-a"), ("carol", "team-a"), ("bob", "team-b")):
        response = client.put(
            "/access/memberships",
            headers=root,
            json={"user_id": user_id, "group_key": group_key},
        )
        assert response.status_code == 200


def test_knowledge_base_group_and_shared_access_is_enforced_by_backend(wm_paths) -> None:
    client = TestClient(create_app(wm_paths, admins={"root"}))
    _configure_groups(client)
    alice = {"X-Agent-Bridge-User": "alice"}
    bob = {"X-Agent-Bridge-User": "bob"}
    carol = {"X-Agent-Bridge-User": "carol"}

    assert client.post(
        "/kbs",
        headers=alice,
        json={"slug": "team-notes", "name": "组内知识", "visibility": "group"},
    ).status_code == 200
    assert client.post(
        "/kbs",
        headers=alice,
        json={"slug": "shared-notes", "name": "共享知识", "visibility": "shared"},
    ).status_code == 200

    assert [item["slug"] for item in client.get("/kbs", headers=bob).json()] == ["shared-notes"]
    assert client.get("/kbs/team-notes/folders", headers=bob).status_code == 403
    assert client.get("/kbs/shared-notes/folders", headers=bob).status_code == 200
    assert client.post(
        "/kbs/shared-notes/folders",
        headers=bob,
        json={"name": "不能跨组新建"},
    ).status_code == 403
    assert client.post(
        "/kbs/team-notes/folders",
        headers=carol,
        json={"name": "同组可新建"},
    ).status_code == 200


def test_capability_and_code_repository_lists_intersect_with_group_access(wm_paths) -> None:
    client = TestClient(create_app(wm_paths, admins={"root"}))
    _configure_groups(client)
    alice = {"X-Agent-Bridge-User": "alice"}
    bob = {"X-Agent-Bridge-User": "bob"}
    carol = {"X-Agent-Bridge-User": "carol"}

    mcp_base = {
        "name": "内部 MCP",
        "endpoint_url": "https://mcp.example.test/mcp",
        "description": "组内能力",
        "tags": ["internal"],
    }
    assert client.post(
        "/capabilities/mcp-services",
        headers=alice,
        json={**mcp_base, "service_key": "team-mcp", "visibility": "group"},
    ).status_code == 200
    assert client.post(
        "/capabilities/mcp-services",
        headers=alice,
        json={**mcp_base, "service_key": "shared-mcp", "visibility": "shared"},
    ).status_code == 200

    assert [item["service_key"] for item in client.get(
        "/capabilities/mcp-services", headers=bob
    ).json()] == ["shared-mcp"]
    assert client.get("/capabilities/mcp-services/team-mcp", headers=bob).status_code == 403
    assert client.post(
        "/capabilities/mcp-services",
        headers=bob,
        json={**mcp_base, "service_key": "shared-mcp", "visibility": "shared"},
    ).status_code == 403
    assert client.post(
        "/capabilities/mcp-services",
        headers=carol,
        json={**mcp_base, "service_key": "shared-mcp", "name": "同组更新"},
    ).status_code == 200

    repo_base = {
        "name": "代码参考",
        "git_url": "https://git.example.test/team/repo.git",
        "branch": "main",
    }
    assert client.post(
        "/code-repo/repositories",
        headers=alice,
        json={**repo_base, "repo_key": "team-code", "visibility": "group"},
    ).status_code == 200
    assert client.post(
        "/code-repo/repositories",
        headers=alice,
        json={**repo_base, "repo_key": "shared-code", "visibility": "shared"},
    ).status_code == 200
    assert [item["repo_key"] for item in client.get(
        "/code-repo/repositories", headers=bob
    ).json()] == ["shared-code"]
    assert client.get("/code-repo/repositories/team-code", headers=bob).status_code == 403
    assert client.post(
        "/code-repo/repositories",
        headers=bob,
        json={**repo_base, "repo_key": "shared-code", "visibility": "shared"},
    ).status_code == 403


class _FakeMcpClient:
    async def call_tool(self, endpoint_url, headers, tool_name, arguments, timeout=150.0):
        return {"is_error": False, "content": [], "structured": {"actor_can_use": True}}


def test_mcp_execution_checks_data_scope_before_transport(wm_paths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    access = AccessControlService(
        store.access_control,
        {"root"},
        create_scoped_resource_registry(store),
    )
    access.bootstrap_admin_memberships()
    access.upsert_group(actor="root", group_key="team-a", name="A 组")
    access.upsert_group(actor="root", group_key="team-b", name="B 组")
    access.set_user_group(actor="root", user_id="alice", group_key="team-a")
    access.set_user_group(actor="root", user_id="carol", group_key="team-a")
    access.set_user_group(actor="root", user_id="bob", group_key="team-b")
    service = CapabilityService(
        store=store,
        admins={"root"},
        access=access,
        mcp_client=_FakeMcpClient(),
    )
    for service_key, visibility in (("team-mcp", "group"), ("shared-mcp", "shared")):
        service.register_service(
            "alice",
            service_key,
            service_key,
            "https://mcp.example.test/mcp",
            {},
            "",
            [],
            visibility=visibility,
        )
        store.upsert_mcp_tool(
            service_key=service_key,
            tool_name="search",
            display_name="search",
            description="",
            input_schema={"type": "object"},
            tool_type="search",
            tags=[],
            examples=[],
        )

    with pytest.raises(AccessDenied, match="其他小组"):
        asyncio.run(service.execute("bob", "team-mcp", "search", {}))
    assert asyncio.run(service.execute("bob", "shared-mcp", "search", {}))["success"]
    assert asyncio.run(service.execute("carol", "team-mcp", "search", {}))["success"]
