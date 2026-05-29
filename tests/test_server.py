from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from wiki_manager.config import load_server_config
from wiki_manager.server import create_app


def test_health(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_api_documentation_is_disabled(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)

    assert client.get("/openapi.json").status_code == 404
    assert client.get("/redoc").status_code == 404


def test_kb_and_doc_api_flow(wm_paths, tmp_path: Path) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    assert client.post("/admin/init", headers={"X-Wiki-User": "root"}).status_code == 200
    response = client.post(
        "/kbs",
        json={"slug": "frontend-docs", "name": "Frontend Docs", "description": ""},
        headers={"X-Wiki-User": "root"},
    )
    assert response.status_code == 200
    assert response.json()["slug"] == "frontend-docs"
    grant = client.post(
        "/kbs/frontend-docs/members",
        json={"linux_user": "alice", "role": "contributor"},
        headers={"X-Wiki-User": "root"},
    )
    assert grant.status_code == 200
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"one")
    with source.open("rb") as handle:
        doc = client.post(
            "/docs",
            data={"kb": ["frontend-docs"], "later": "true"},
            files={"file": ("Guide.pdf", handle, "application/pdf")},
            headers={"X-Wiki-User": "alice"},
        )
    assert doc.status_code == 200
    assert doc.json()["slug"] == "guide"
    docs = client.get("/docs?kb=frontend-docs", headers={"X-Wiki-User": "alice"})
    assert docs.status_code == 200
    assert docs.json()[0]["slug"] == "guide"


def test_upload_temp_files_are_cleaned_up(wm_paths, tmp_path: Path) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    assert client.post("/admin/init", headers={"X-Wiki-User": "root"}).status_code == 200
    assert (
        client.post(
            "/kbs",
            json={"slug": "frontend-docs", "name": "Frontend Docs", "description": ""},
            headers={"X-Wiki-User": "root"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/kbs/frontend-docs/members",
            json={"linux_user": "alice", "role": "contributor"},
            headers={"X-Wiki-User": "root"},
        ).status_code
        == 200
    )

    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"one")
    with source.open("rb") as handle:
        doc = client.post(
            "/docs",
            data={"kb": ["frontend-docs"], "later": "true"},
            files={"file": ("nested/path/Guide.pdf", handle, "application/pdf")},
            headers={"X-Wiki-User": "alice"},
        )
    assert doc.status_code == 200
    assert not [path for path in wm_paths.run_dir.iterdir() if path.is_file()]
    assert not list(wm_paths.run_dir.glob("upload-*"))

    source.write_bytes(b"two")
    with source.open("rb") as handle:
        update = client.post(
            f"/docs/{doc.json()['slug']}/versions",
            data={"later": "true"},
            files={"file": ("other:name?.pdf", handle, "application/pdf")},
            headers={"X-Wiki-User": "alice"},
        )
    assert update.status_code == 200
    assert not [path for path in wm_paths.run_dir.iterdir() if path.is_file()]
    assert not list(wm_paths.run_dir.glob("upload-*"))


def test_invisible_kb_returns_404(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    client.post("/admin/init", headers={"X-Wiki-User": "root"})
    client.post("/kbs", json={"slug": "frontend-docs", "name": "Frontend Docs", "description": ""}, headers={"X-Wiki-User": "root"})
    response = client.get("/docs?kb=frontend-docs", headers={"X-Wiki-User": "alice"})
    assert response.status_code == 404


def test_purge_api_requires_confirmation_body(wm_paths, tmp_path: Path) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    assert client.post("/admin/init", headers={"X-Wiki-User": "root"}).status_code == 200
    assert (
        client.post(
            "/kbs",
            json={"slug": "frontend-docs", "name": "Frontend Docs", "description": ""},
            headers={"X-Wiki-User": "root"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/kbs/frontend-docs/members",
            json={"linux_user": "alice", "role": "contributor"},
            headers={"X-Wiki-User": "root"},
        ).status_code
        == 200
    )
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"one")
    with source.open("rb") as handle:
        doc = client.post(
            "/docs",
            data={"kb": ["frontend-docs"], "later": "true"},
            files={"file": ("Guide.pdf", handle, "application/pdf")},
            headers={"X-Wiki-User": "alice"},
        )
    assert doc.status_code == 200

    missing_confirm = client.post(f"/docs/{doc.json()['slug']}/purge", json={}, headers={"X-Wiki-User": "alice"})
    confirmed = client.post(
        f"/docs/{doc.json()['slug']}/purge",
        json={"confirm": True},
        headers={"X-Wiki-User": "alice"},
    )

    assert missing_confirm.status_code == 400
    assert confirmed.status_code == 200


def test_create_app_refreshes_admins_from_config_per_request(wm_paths) -> None:
    config = load_server_config(wm_paths)
    assert config.admins == {"root"}
    app = create_app(paths=wm_paths, admins=None)
    client = TestClient(app)
    assert client.post("/admin/init", headers={"X-Wiki-User": "root"}).status_code == 200

    wm_paths.server_config_path.write_text(
        'host = "127.0.0.1"\nport = 8765\nadmins = ["alice"]\n',
        encoding="utf-8",
    )

    response = client.post(
        "/kbs",
        json={"slug": "frontend-docs", "name": "Frontend Docs", "description": ""},
        headers={"X-Wiki-User": "alice"},
    )

    assert response.status_code == 200
    assert response.json()["slug"] == "frontend-docs"
