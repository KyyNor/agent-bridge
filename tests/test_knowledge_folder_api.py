from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from agent_bridge.api.app import create_app


def _client(wm_paths) -> TestClient:
    wm_paths.config_dir.mkdir(parents=True, exist_ok=True)
    wm_paths.server_config_path.write_text('[backends.mock]\nbackend_type = "mock"\n', encoding="utf-8")
    return TestClient(create_app(paths=wm_paths, admins={"root"}))


def _headers() -> dict[str, str]:
    return {"X-Agent-Bridge-User": "root"}


def test_folder_api_crud_and_delete_confirmation(wm_paths, tmp_path: Path) -> None:
    client = _client(wm_paths)
    headers = _headers()
    assert client.post("/admin/init", headers=headers).status_code == 200
    assert client.post("/kbs", json={"slug": "docs", "name": "Docs"}, headers=headers).status_code == 200

    root = client.get("/kbs/docs/folders", headers=headers).json()[0]
    created = client.post(
        "/kbs/docs/folders",
        json={"parent_folder_id": root["id"], "name": "Guides"},
        headers=headers,
    )
    assert created.status_code == 200
    folder = created.json()
    assert folder["path"] == "Guides"

    renamed = client.patch(
        f"/kbs/docs/folders/{folder['id']}",
        json={"name": "Reference"},
        headers=headers,
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Reference"

    source = tmp_path / "Guide.md"
    source.write_bytes(b"guide")
    with source.open("rb") as handle:
        uploaded = client.post(
            "/docs",
            data={"kb": ["docs"], "folder_id": str(folder["id"]), "later": "true"},
            files={"file": ("Guide.md", handle, "text/markdown")},
            headers=headers,
        )
    assert uploaded.status_code == 200
    doc_slug = uploaded.json()["slug"]
    assert client.get("/docs", params={"kb": "docs", "folder_id": folder["id"]}, headers=headers).json()[0]["slug"] == doc_slug

    pending = client.delete(f"/kbs/docs/folders/{folder['id']}", headers=headers)
    assert pending.status_code == 409
    assert pending.json()["detail"]["directory_count"] == 1
    assert pending.json()["detail"]["file_count"] == 1

    deleted = client.request(
        "DELETE",
        f"/kbs/docs/folders/{folder['id']}",
        json={"confirm": True},
        headers=headers,
    )
    assert deleted.status_code == 200
    assert deleted.json()["directory_count"] == 1
    assert client.get("/docs", params={"kb": "docs"}, headers=headers).json() == []

    root_delete = client.request(
        "DELETE",
        f"/kbs/docs/folders/{root['id']}",
        json={"confirm": True},
        headers=headers,
    )
    assert root_delete.status_code == 400


def test_folder_delete_confirmation_recounts_live_subtree(wm_paths, tmp_path: Path) -> None:
    client = _client(wm_paths)
    headers = _headers()
    assert client.post("/admin/init", headers=headers).status_code == 200
    assert client.post("/kbs", json={"slug": "docs", "name": "Docs"}, headers=headers).status_code == 200

    root = client.get("/kbs/docs/folders", headers=headers).json()[0]
    parent = client.post(
        "/kbs/docs/folders",
        json={"parent_folder_id": root["id"], "name": "Parent"},
        headers=headers,
    ).json()
    first_source = tmp_path / "first.md"
    first_source.write_bytes(b"first")
    with first_source.open("rb") as handle:
        uploaded = client.post(
            "/docs",
            data={"kb": ["docs"], "folder_id": str(parent["id"]), "later": "true"},
            files={"file": ("first.md", handle, "text/markdown")},
            headers=headers,
        )
    assert uploaded.status_code == 200

    preview = client.delete(f"/kbs/docs/folders/{parent['id']}", headers=headers)
    assert preview.status_code == 409
    assert preview.json()["detail"]["directory_count"] == 1
    assert preview.json()["detail"]["file_count"] == 1

    child = client.post(
        "/kbs/docs/folders",
        json={"parent_folder_id": parent["id"], "name": "Child"},
        headers=headers,
    ).json()
    second_source = tmp_path / "second.md"
    second_source.write_bytes(b"second")
    with second_source.open("rb") as handle:
        uploaded = client.post(
            "/docs",
            data={"kb": ["docs"], "folder_id": str(child["id"]), "later": "true"},
            files={"file": ("second.md", handle, "text/markdown")},
            headers=headers,
        )
    assert uploaded.status_code == 200

    deleted = client.request(
        "DELETE",
        f"/kbs/docs/folders/{parent['id']}",
        json={"confirm": True},
        headers=headers,
    )
    assert deleted.status_code == 200
    assert deleted.json()["directory_count"] == 2
    assert deleted.json()["file_count"] == 2
    assert client.get("/docs", params={"kb": "docs"}, headers=headers).json() == []


def test_document_placement_attach_and_scoped_delete_api(wm_paths, tmp_path: Path) -> None:
    client = _client(wm_paths)
    headers = _headers()
    client.post("/admin/init", headers=headers)
    client.post("/kbs", json={"slug": "kb-a", "name": "A"}, headers=headers)
    client.post("/kbs", json={"slug": "kb-b", "name": "B"}, headers=headers)
    root_a = client.get("/kbs/kb-a/folders", headers=headers).json()[0]
    root_b = client.get("/kbs/kb-b/folders", headers=headers).json()[0]
    folder_a = client.post(
        "/kbs/kb-a/folders",
        json={"parent_folder_id": root_a["id"], "name": "A"},
        headers=headers,
    ).json()
    source = tmp_path / "Shared.md"
    source.write_bytes(b"shared")
    with source.open("rb") as handle:
        uploaded = client.post(
            "/docs",
            data={"kb": ["kb-a"], "folder_id": str(root_a["id"]), "later": "true"},
            files={"file": ("Shared.md", handle, "text/markdown")},
            headers=headers,
        )
    assert uploaded.status_code == 200
    slug = uploaded.json()["slug"]

    attached = client.post(
        f"/docs/{slug}/attach",
        json={"kb": "kb-b", "folder_id": root_b["id"]},
        headers=headers,
    )
    assert attached.status_code == 200
    moved = client.patch(
        f"/docs/{slug}/placement",
        json={"kb": "kb-a", "folder_id": folder_a["id"]},
        headers=headers,
    )
    assert moved.status_code == 200
    assert moved.json()["folder_id"] == folder_a["id"]

    wrong_kb = client.patch(
        f"/docs/{slug}/placement",
        json={"kb": "kb-a", "folder_id": root_b["id"]},
        headers=headers,
    )
    assert wrong_kb.status_code == 404

    removed = client.post(f"/kbs/kb-a/docs/{slug}/delete", headers=headers)
    assert removed.status_code == 200
    assert client.get("/docs", params={"kb": "kb-a"}, headers=headers).json() == []
    assert client.get("/docs", params={"kb": "kb-b"}, headers=headers).json()[0]["slug"] == slug


def test_upload_api_preserves_browser_folder_relative_path(wm_paths, tmp_path: Path) -> None:
    client = _client(wm_paths)
    headers = _headers()
    client.post("/admin/init", headers=headers)
    client.post("/kbs", json={"slug": "docs", "name": "Docs"}, headers=headers)
    source = tmp_path / "Guide.md"
    source.write_bytes(b"guide")

    with source.open("rb") as handle:
        uploaded = client.post(
            "/docs",
            data={"kb": ["docs"], "relative_path": r"Guides\\API\\Guide.md", "later": "true"},
            files={"file": ("Guide.md", handle, "text/markdown")},
            headers=headers,
        )

    assert uploaded.status_code == 200, uploaded.text
    slug = uploaded.json()["slug"]
    docs = client.get("/docs", params={"kb": "docs"}, headers=headers).json()
    assert docs[0]["folder_path"] == "Guides/API"
    assert not docs[0]["folder_path"].startswith("root/")
    detail = client.get(f"/docs/{slug}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["versions"][0]["original_filename"] == "Guides/API/Guide.md"


def test_upload_api_rejects_relative_path_traversal(wm_paths, tmp_path: Path) -> None:
    client = _client(wm_paths)
    headers = _headers()
    client.post("/admin/init", headers=headers)
    client.post("/kbs", json={"slug": "docs", "name": "Docs"}, headers=headers)
    source = tmp_path / "Guide.md"
    source.write_bytes(b"guide")

    with source.open("rb") as handle:
        uploaded = client.post(
            "/docs",
            data={"kb": ["docs"], "relative_path": "../Guide.md", "later": "true"},
            files={"file": ("Guide.md", handle, "text/markdown")},
            headers=headers,
        )

    assert uploaded.status_code == 400
    assert client.get("/docs", params={"kb": "docs"}, headers=headers).json() == []
