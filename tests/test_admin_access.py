from __future__ import annotations

from fastapi.testclient import TestClient

from agent_bridge.api.app import create_app


PASSWORD = "internal-admin-123"
NEW_PASSWORD = "internal-admin-456"


def test_bare_browser_can_initialize_password_and_switch_to_admin(wm_paths) -> None:
    app = create_app(wm_paths, admins={"root"})
    client = TestClient(app)

    assert client.get("/auth/admin/status").json() == {
        "configured": False,
        "active": False,
        "subject_user_id": None,
    }
    assert client.get("/access/groups").status_code == 401

    login = client.post("/auth/admin/session", json={"password": PASSWORD})

    assert login.status_code == 200
    assert login.json() == {
        "configured": True,
        "active": True,
        "initialized": True,
        "subject_user_id": "anonymous",
    }
    assert "agent_bridge_admin=" in login.headers["set-cookie"]
    assert "HttpOnly" in login.headers["set-cookie"]
    assert client.get("/access/groups").status_code == 200
    assert client.get("/access/me").json()["is_maintenance_admin"] is True

    with app.state.agent_bridge_service.store.connect() as conn:
        stored = conn.execute(
            "SELECT password_hash FROM admin_access_config WHERE id = 1"
        ).fetchone()[0]
    assert stored.startswith("pbkdf2_sha256$")
    assert PASSWORD not in stored


def test_existing_identity_can_switch_without_replacing_sso_cookie(wm_paths) -> None:
    app = create_app(wm_paths, admins={"root"})
    client = TestClient(app)

    login = client.post(
        "/auth/admin/session",
        headers={"X-Agent-Bridge-User": "alice"},
        json={"password": PASSWORD},
    )

    assert login.status_code == 200
    assert login.json()["subject_user_id"] == "alice"
    assert client.get("/auth/admin/status").json() == {
        "configured": True,
        "active": True,
        "subject_user_id": "alice",
    }
    client.delete("/auth/admin/session")
    assert client.get(
        "/access/me", headers={"X-Agent-Bridge-User": "alice"}
    ).json()["is_maintenance_admin"] is False


def test_configured_password_rejects_wrong_value_and_accepts_correct_value(wm_paths) -> None:
    app = create_app(wm_paths, admins={"root"})
    first = TestClient(app)
    assert first.post("/auth/admin/session", json={"password": PASSWORD}).status_code == 200

    second = TestClient(app)
    assert second.post(
        "/auth/admin/session", json={"password": "wrong-password"}
    ).status_code == 401
    assert second.get("/access/groups").status_code == 401
    assert second.post(
        "/auth/admin/session", json={"password": PASSWORD}
    ).status_code == 200
    assert second.get("/access/groups").status_code == 200


def test_changing_password_invalidates_existing_admin_sessions(wm_paths) -> None:
    app = create_app(wm_paths, admins={"root"})
    client = TestClient(app)
    client.post("/auth/admin/session", json={"password": PASSWORD})

    changed = client.put(
        "/auth/admin/password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )

    assert changed.status_code == 200
    assert changed.json() == {"updated": True}
    assert client.get("/auth/admin/status").json()["active"] is False
    assert client.get("/access/groups").status_code == 401
    assert client.post(
        "/auth/admin/session", json={"password": PASSWORD}
    ).status_code == 401
    assert client.post(
        "/auth/admin/session", json={"password": NEW_PASSWORD}
    ).status_code == 200


def test_password_change_requires_admin_identity_and_current_password(wm_paths) -> None:
    app = create_app(wm_paths, admins={"root"})
    client = TestClient(app)
    client.post("/auth/admin/session", json={"password": PASSWORD})
    client.delete("/auth/admin/session")

    denied = client.put(
        "/auth/admin/password",
        headers={"X-Agent-Bridge-User": "alice"},
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )
    wrong = client.put(
        "/auth/admin/password",
        headers={"X-Agent-Bridge-User": "root"},
        json={"current_password": "wrong-password", "new_password": NEW_PASSWORD},
    )

    assert denied.status_code == 403
    assert wrong.status_code == 401


def test_password_admin_uses_existing_cross_group_data_bypass(wm_paths) -> None:
    app = create_app(wm_paths, admins={"root"})
    setup = TestClient(app)
    root = {"X-Agent-Bridge-User": "root"}
    setup.post(
        "/access/groups",
        headers=root,
        json={"group_key": "team-a", "name": "A 组"},
    )
    setup.post(
        "/access/groups",
        headers=root,
        json={"group_key": "team-b", "name": "B 组"},
    )
    setup.put(
        "/access/memberships",
        headers=root,
        json={"user_id": "alice", "group_key": "team-a"},
    )
    setup.put(
        "/access/memberships",
        headers=root,
        json={"user_id": "bob", "group_key": "team-b"},
    )
    created = setup.post(
        "/kbs",
        headers={"X-Agent-Bridge-User": "alice"},
        json={"slug": "private-a", "name": "A 组私有知识库", "visibility": "group"},
    )
    assert created.status_code == 200
    assert setup.get(
        "/kbs", headers={"X-Agent-Bridge-User": "bob"}
    ).json() == []

    password_admin = TestClient(app)
    password_admin.post("/auth/admin/session", json={"password": PASSWORD})
    visible = password_admin.get("/kbs")

    assert visible.status_code == 200
    assert [item["slug"] for item in visible.json()] == ["private-a"]
