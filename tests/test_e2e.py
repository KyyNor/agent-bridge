from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from wiki_manager.server import create_app


def test_phase_one_smoke_flow(wm_paths, tmp_path: Path) -> None:
    client = TestClient(create_app(paths=wm_paths, admins={"root"}))
    assert client.post("/admin/init", headers={"X-Wiki-User": "root"}).status_code == 200
    assert client.post(
        "/kbs",
        json={"slug": "frontend-docs", "name": "Frontend Docs", "description": ""},
        headers={"X-Wiki-User": "root"},
    ).status_code == 200
    assert client.post(
        "/kbs/frontend-docs/members",
        json={"linux_user": "alice", "role": "contributor"},
        headers={"X-Wiki-User": "root"},
    ).status_code == 200

    v1 = tmp_path / "Guide.pdf"
    v2 = tmp_path / "Guide-v2.pdf"
    v1.write_bytes(b"one")
    v2.write_bytes(b"two")

    with v1.open("rb") as handle:
        added = client.post(
            "/docs",
            data={"kb": ["frontend-docs"], "later": "true"},
            files={"file": ("Guide.pdf", handle, "application/pdf")},
            headers={"X-Wiki-User": "alice"},
        )
    assert added.status_code == 200
    assert added.json()["current_version_no"] == 1

    synced = client.post("/sync", json={"all_users": False}, headers={"X-Wiki-User": "alice"})
    assert synced.status_code == 200
    assert synced.json()["processed"] == 1

    with v2.open("rb") as handle:
        updated = client.post(
            "/docs/guide/versions",
            data={"later": "true"},
            files={"file": ("Guide-v2.pdf", handle, "application/pdf")},
            headers={"X-Wiki-User": "alice"},
        )
    assert updated.status_code == 200
    assert updated.json()["current_version_no"] == 2

    deleted = client.post("/docs/guide/delete", headers={"X-Wiki-User": "alice"})
    assert deleted.status_code == 200
    status = client.get("/status", headers={"X-Wiki-User": "alice"})
    assert status.status_code == 200
    assert len(status.json()["jobs"]) >= 3
