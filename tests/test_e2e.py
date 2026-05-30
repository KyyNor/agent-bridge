from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from wiki_manager.server import create_app


def _write_mock_backend_config(wm_paths) -> None:
    wm_paths.config_dir.mkdir(parents=True, exist_ok=True)
    wm_paths.server_config_path.write_text(
        '[backends.mock]\nbackend_type = "mock"\n',
        encoding="utf-8",
    )


def _jobs_by_operation(client: TestClient) -> dict[str, list[dict]]:
    response = client.get("/status", headers={"X-Wiki-User": "alice"})
    assert response.status_code == 200
    jobs = response.json()["jobs"]
    grouped: dict[str, list[dict]] = {}
    for job in jobs:
        grouped.setdefault(job["operation"], []).append(job)
    return grouped


def test_phase_one_smoke_flow(wm_paths, tmp_path: Path) -> None:
    _write_mock_backend_config(wm_paths)
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
    jobs = _jobs_by_operation(client)
    assert jobs["create"][-1]["status"] == "pending"

    synced = client.post("/sync", json={"all_users": False}, headers={"X-Wiki-User": "alice"})
    assert synced.status_code == 200
    assert synced.json()["processed"] == 1
    jobs = _jobs_by_operation(client)
    assert jobs["create"][-1]["status"] == "succeeded"

    with v2.open("rb") as handle:
        updated = client.post(
            "/docs/guide/versions",
            data={"later": "true"},
            files={"file": ("Guide-v2.pdf", handle, "application/pdf")},
            headers={"X-Wiki-User": "alice"},
        )
    assert updated.status_code == 200
    assert updated.json()["current_version_no"] == 2
    jobs = _jobs_by_operation(client)
    assert jobs["update"][-1]["status"] == "pending"

    synced = client.post("/sync", json={"all_users": False}, headers={"X-Wiki-User": "alice"})
    assert synced.status_code == 200
    assert synced.json()["processed"] == 1
    jobs = _jobs_by_operation(client)
    assert jobs["update"][-1]["status"] == "succeeded"

    deleted = client.post("/docs/guide/delete", headers={"X-Wiki-User": "alice"})
    assert deleted.status_code == 200
    jobs = _jobs_by_operation(client)
    assert jobs["delete"][-1]["status"] == "pending"

    synced = client.post("/sync", json={"all_users": False}, headers={"X-Wiki-User": "alice"})
    assert synced.status_code == 200
    assert synced.json()["processed"] == 1
    jobs = _jobs_by_operation(client)
    operations = [job["operation"] for job_list in jobs.values() for job in job_list]
    assert operations == ["create", "update", "delete"]
    assert jobs["delete"][-1]["status"] == "succeeded"
    assert [jobs[operation][-1]["status"] for operation in ("create", "update", "delete")] == [
        "succeeded",
        "succeeded",
        "succeeded",
    ]
