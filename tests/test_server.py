from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from agent_bridge.core.config import load_server_config
from agent_bridge.api.app import create_app


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
    assert client.post("/api/v1/admin/init", headers={"X-Agent-Bridge-User": "root"}).status_code == 200
    response = client.post(
        "/api/v1/kbs",
        json={"slug": "frontend-docs", "name": "Frontend Docs", "description": ""},
        headers={"X-Agent-Bridge-User": "root"},
    )
    assert response.status_code == 200
    assert response.json()["slug"] == "frontend-docs"
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"one")
    with source.open("rb") as handle:
        doc = client.post(
            "/api/v1/docs",
            data={"kb": ["frontend-docs"], "later": "true"},
            files={"file": ("Guide.pdf", handle, "application/pdf")},
            headers={"X-Agent-Bridge-User": "root"},
        )
    assert doc.status_code == 200
    assert doc.json()["slug"] == "guide"
    docs = client.get("/api/v1/docs?kb=frontend-docs", headers={"X-Agent-Bridge-User": "root"})
    assert docs.status_code == 200
    assert docs.json()[0]["slug"] == "guide"


def test_upload_temp_files_are_cleaned_up(wm_paths, tmp_path: Path) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    assert client.post("/api/v1/admin/init", headers={"X-Agent-Bridge-User": "root"}).status_code == 200
    assert (
        client.post(
            "/api/v1/kbs",
            json={"slug": "frontend-docs", "name": "Frontend Docs", "description": ""},
            headers={"X-Agent-Bridge-User": "root"},
        ).status_code
        == 200
    )
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"one")
    with source.open("rb") as handle:
        doc = client.post(
            "/api/v1/docs",
            data={"kb": ["frontend-docs"], "later": "true"},
            files={"file": ("nested/path/Guide.pdf", handle, "application/pdf")},
            headers={"X-Agent-Bridge-User": "root"},
        )
    assert doc.status_code == 200
    assert not [path for path in wm_paths.run_dir.iterdir() if path.is_file()]
    assert not list(wm_paths.run_dir.glob("upload-*"))

    source.write_bytes(b"two")
    with source.open("rb") as handle:
        update = client.post(
            f"/api/v1/docs/{doc.json()['slug']}/versions",
            data={"later": "true"},
            files={"file": ("other:name?.pdf", handle, "application/pdf")},
            headers={"X-Agent-Bridge-User": "root"},
        )
    assert update.status_code == 200
    assert not [path for path in wm_paths.run_dir.iterdir() if path.is_file()]
    assert not list(wm_paths.run_dir.glob("upload-*"))


def test_invisible_kb_returns_404(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    client.post("/api/v1/admin/init", headers={"X-Agent-Bridge-User": "root"})
    client.post("/api/v1/kbs", json={"slug": "frontend-docs", "name": "Frontend Docs", "description": ""}, headers={"X-Agent-Bridge-User": "root"})
    response = client.get("/api/v1/docs?kb=frontend-docs", headers={"X-Agent-Bridge-User": "alice"})
    assert response.status_code == 403


def test_purge_api_requires_confirmation_body(wm_paths, tmp_path: Path) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    assert client.post("/api/v1/admin/init", headers={"X-Agent-Bridge-User": "root"}).status_code == 200
    assert (
        client.post(
            "/api/v1/kbs",
            json={"slug": "frontend-docs", "name": "Frontend Docs", "description": ""},
            headers={"X-Agent-Bridge-User": "root"},
        ).status_code
        == 200
    )
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"one")
    with source.open("rb") as handle:
        doc = client.post(
            "/api/v1/docs",
            data={"kb": ["frontend-docs"], "later": "true"},
            files={"file": ("Guide.pdf", handle, "application/pdf")},
            headers={"X-Agent-Bridge-User": "root"},
        )
    assert doc.status_code == 200

    missing_confirm = client.post(f"/api/v1/docs/{doc.json()['slug']}/purge", json={}, headers={"X-Agent-Bridge-User": "root"})
    confirmed = client.post(
        f"/api/v1/docs/{doc.json()['slug']}/purge",
        json={"confirm": True},
        headers={"X-Agent-Bridge-User": "root"},
    )

    assert missing_confirm.status_code == 400
    assert confirmed.status_code == 200


def test_create_app_refreshes_admins_from_config_per_request(wm_paths) -> None:
    config = load_server_config(wm_paths)
    assert config.admins == {"root"}
    app = create_app(paths=wm_paths, admins=None)
    client = TestClient(app)
    assert client.post("/api/v1/admin/init", headers={"X-Agent-Bridge-User": "root"}).status_code == 200

    wm_paths.server_config_path.write_text(
        'host = "127.0.0.1"\nport = 8765\nadmins = ["alice"]\n',
        encoding="utf-8",
    )

    response = client.post(
        "/api/v1/kbs",
        json={"slug": "frontend-docs", "name": "Frontend Docs", "description": ""},
        headers={"X-Agent-Bridge-User": "alice"},
    )

    assert response.status_code == 200
    assert response.json()["slug"] == "frontend-docs"


def test_backends_endpoint(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    response = client.get("/api/v1/backends", headers={"X-Agent-Bridge-User": "root"})
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_doc_with_backend_filter(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    client.post("/api/v1/admin/init", headers={"X-Agent-Bridge-User": "root"})
    response = client.get("/api/v1/docs/nonexistent?backend=mock", headers={"X-Agent-Bridge-User": "root"})
    assert response.status_code in (200, 404)


def test_status_with_backend_filter(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    client.post("/api/v1/admin/init", headers={"X-Agent-Bridge-User": "root"})
    response = client.get("/api/v1/status?backend=mock", headers={"X-Agent-Bridge-User": "root"})
    assert response.status_code == 200


def test_status_requires_admin(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    client.post("/api/v1/admin/init", headers={"X-Agent-Bridge-User": "root"})
    response = client.get("/api/v1/status", headers={"X-Agent-Bridge-User": "alice"})
    assert response.status_code == 403


def test_sync_with_backend_filter(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    client.post("/api/v1/admin/init", headers={"X-Agent-Bridge-User": "root"})
    response = client.post("/api/v1/sync", json={"all_users": False}, params={"backend": "mock"}, headers={"X-Agent-Bridge-User": "root"})
    assert response.status_code == 200


def test_search_endpoint(wm_paths) -> None:
    wm_paths.server_config_path.parent.mkdir(parents=True, exist_ok=True)
    wm_paths.server_config_path.write_text(
        'host = "127.0.0.1"\nport = 8765\nadmins = ["root"]\n\n[backends.mock]\nbackend_type = "mock"\n',
        encoding="utf-8",
    )
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    client.post("/api/v1/admin/init", headers={"X-Agent-Bridge-User": "root"})
    client.post("/api/v1/kbs", json={"slug": "test-kb", "name": "Test KB"}, headers={"X-Agent-Bridge-User": "root"})
    response = client.get("/api/v1/search", params={"kb": "test-kb", "q": "hello"}, headers={"X-Agent-Bridge-User": "root"})
    assert response.status_code == 200
    data = response.json()
    assert "results" in data


def test_ask_endpoint(wm_paths) -> None:
    wm_paths.server_config_path.parent.mkdir(parents=True, exist_ok=True)
    wm_paths.server_config_path.write_text(
        'host = "127.0.0.1"\nport = 8765\nadmins = ["root"]\n\n[backends.mock]\nbackend_type = "mock"\n',
        encoding="utf-8",
    )
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    client.post("/api/v1/admin/init", headers={"X-Agent-Bridge-User": "root"})
    client.post("/api/v1/kbs", json={"slug": "test-kb", "name": "Test KB"}, headers={"X-Agent-Bridge-User": "root"})
    response = client.post("/api/v1/ask", json={"kb": "test-kb", "question": "what is X?"}, headers={"X-Agent-Bridge-User": "root"})
    assert response.status_code == 200
    data = response.json()
    assert "answer" in data


def test_search_missing_kb(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    client.post("/api/v1/admin/init", headers={"X-Agent-Bridge-User": "root"})
    response = client.get("/api/v1/search", params={"kb": "nonexistent", "q": "hello"}, headers={"X-Agent-Bridge-User": "root"})
    assert response.status_code == 404
