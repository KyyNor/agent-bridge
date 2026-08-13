from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from agent_bridge.access_control.service import (
    AccessControlService,
    ResourceScope,
    ResourceVisibility,
)
from agent_bridge.api.app import create_app
from agent_bridge.core.domain import ValidationError
from agent_bridge.storage.sqlite import SQLiteStore


def _access_service(wm_paths) -> tuple[SQLiteStore, AccessControlService]:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    access = AccessControlService(store.access_control, {"root"})
    access.bootstrap_admin_memberships()
    access.upsert_group(actor="root", group_key="team-a", name="A 组")
    access.upsert_group(actor="root", group_key="team-b", name="B 组")
    access.set_user_group(actor="root", user_id="alice", group_key="team-a")
    access.set_user_group(actor="root", user_id="bob", group_key="team-b")
    return store, access


def test_group_scope_policy_and_membership_move_do_not_move_resource(wm_paths) -> None:
    _, access = _access_service(wm_paths)
    group_scope = ResourceScope("team-a", ResourceVisibility.group)
    shared_scope = ResourceScope("team-a", ResourceVisibility.shared)

    assert access.can_read(actor="alice", scope=group_scope)
    assert access.can_write(actor="alice", scope=group_scope)
    assert not access.can_read(actor="bob", scope=group_scope)
    assert access.can_read(actor="bob", scope=shared_scope)
    assert not access.can_write(actor="bob", scope=shared_scope)
    assert access.can_write(actor="root", scope=shared_scope)

    access.set_user_group(actor="root", user_id="alice", group_key="team-b")
    assert not access.can_write(actor="alice", scope=group_scope)


def test_user_without_group_can_read_shared_but_cannot_create(wm_paths) -> None:
    import pytest

    from agent_bridge.core.domain import AccessDenied

    _, access = _access_service(wm_paths)
    assert access.can_read(
        actor="carol",
        scope=ResourceScope("team-a", ResourceVisibility.shared),
    )
    with pytest.raises(AccessDenied, match="尚未分配小组"):
        access.new_resource_scope(actor="carol", visibility="group")


def test_only_shareable_resource_types_accept_shared_visibility(wm_paths) -> None:
    import pytest

    from agent_bridge.access_control.resources import ScopedResourceType
    from agent_bridge.core.domain import ValidationError

    _, access = _access_service(wm_paths)
    shared = access.new_resource_scope(
        actor="alice",
        visibility="shared",
        resource_type=ScopedResourceType.business_ledger,
    )
    assert shared.visibility is ResourceVisibility.shared
    with pytest.raises(ValidationError, match="不允许共享"):
        access.new_resource_scope(
            actor="alice",
            visibility="shared",
            resource_type=ScopedResourceType.capability_profile,
        )


def test_access_schema_keeps_unassigned_legacy_resources_private(wm_paths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.init_schema()

    with store.connect() as conn:
        conn.execute(
            "INSERT INTO knowledge_bases (slug, name, created_by) VALUES ('legacy', 'Legacy', 'root')"
        )
        row = conn.execute(
            "SELECT owner_group_key, visibility FROM knowledge_bases WHERE slug = 'legacy'"
        ).fetchone()
        tables = {
            name: {item[1] for item in conn.execute(f"PRAGMA table_info({name})")}
            for name in ("knowledge_bases", "mcp_services", "openapi_services", "code_repositories")
        }

    assert dict(row) == {"owner_group_key": "", "visibility": "group"}
    assert all({"owner_group_key", "visibility"} <= columns for columns in tables.values())
    assert "created_by" in tables["code_repositories"]


def test_access_group_management_api(wm_paths) -> None:
    client = TestClient(create_app(wm_paths, admins={"root"}))
    root = {"X-Agent-Bridge-User": "root"}

    group_response = client.post(
        "/api/v1/access/groups",
        headers=root,
        json={"group_key": "data-team", "name": "数据组", "description": "内部分析"},
    )
    assert group_response.status_code == 200
    membership_response = client.put(
        "/api/v1/access/memberships",
        headers=root,
        json={"user_id": "alice", "group_key": "data-team"},
    )
    assert membership_response.status_code == 200
    assert membership_response.json()["group_key"] == "data-team"

    me_response = client.get("/api/v1/access/me", headers={"X-Agent-Bridge-User": "alice"})
    assert me_response.status_code == 200
    assert me_response.json() == {
        "user_id": "alice",
        "group_key": "data-team",
        "group_name": "数据组",
        "is_maintenance_admin": False,
    }
    denied = client.get("/api/v1/access/groups", headers={"X-Agent-Bridge-User": "alice"})
    assert denied.status_code == 403


def test_user_directory_keeps_user_when_membership_is_cleared(wm_paths) -> None:
    _, access = _access_service(wm_paths)

    access.create_user(actor="root", user_id="carol")
    access.set_user_group(actor="root", user_id="carol", group_key="team-a")
    access.set_user_group(actor="root", user_id="carol", group_key=None)

    users = {user["user_id"]: user for user in access.list_users("root")}
    assert users["carol"]["group_key"] is None
    assert users["carol"]["group_name"] is None


def test_group_with_members_cannot_be_deleted_until_memberships_are_handled(wm_paths) -> None:
    _, access = _access_service(wm_paths)

    with pytest.raises(ValidationError, match="仍有 1 名成员"):
        access.delete_group(actor="root", group_key="team-a")

    access.set_user_group(actor="root", user_id="alice", group_key=None)
    assert access.delete_group(actor="root", group_key="team-a") == {"deleted": True}


def test_user_directory_api_and_nullable_group_assignment(wm_paths) -> None:
    client = TestClient(create_app(wm_paths, admins={"root"}))
    root = {"X-Agent-Bridge-User": "root"}
    assert client.post(
        "/api/v1/access/users", headers=root, json={"user_id": "carol"}
    ).status_code == 200
    assert client.post(
        "/api/v1/access/groups",
        headers=root,
        json={"group_key": "team-a", "name": "A 组"},
    ).status_code == 200

    assigned = client.put(
        "/api/v1/access/memberships",
        headers=root,
        json={"user_id": "carol", "group_key": "team-a"},
    )
    assert assigned.status_code == 200
    assert assigned.json()["group_key"] == "team-a"

    cleared = client.put(
        "/api/v1/access/memberships",
        headers=root,
        json={"user_id": "carol", "group_key": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["group_key"] is None
    users = {
        item["user_id"]: item
        for item in client.get("/api/v1/access/users", headers=root).json()
    }
    assert users["carol"]["group_key"] is None
    assert client.delete("/api/v1/access/groups/team-a", headers=root).json() == {"deleted": True}
