from __future__ import annotations

import io
import zipfile

from fastapi.testclient import TestClient

from agent_bridge.api.app import create_app


def _client(wm_paths) -> TestClient:
    wm_paths.config_dir.mkdir(parents=True, exist_ok=True)
    wm_paths.server_config_path.write_text(
        '[backends.mock]\nbackend_type = "mock"\n',
        encoding="utf-8",
    )
    return TestClient(create_app(paths=wm_paths, admins={"root"}))


def _headers() -> dict[str, str]:
    return {"X-Agent-Bridge-User": "root"}


def _zip_bytes(members: list[tuple[str, bytes]]) -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        for name, content in members:
            archive.writestr(name, content)
    return payload.getvalue()


def _create_kb(client: TestClient, slug: str) -> dict:
    response = client.post(
        "/api/v1/kbs",
        json={"slug": slug, "name": slug.upper()},
        headers=_headers(),
    )
    assert response.status_code == 200, response.text
    return client.get(f"/api/v1/kbs/{slug}/folders", headers=_headers()).json()[0]


def _upload_zip(client: TestClient, filename: str, payload: bytes) -> dict:
    response = client.post(
        "/api/v1/docs",
        data={"kb": ["docs"], "later": "true"},
        files={"file": (filename, payload, "application/zip")},
        headers=_headers(),
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_nested_zip_browse_exposes_direct_archive_children_and_document_contract(wm_paths) -> None:
    client = _client(wm_paths)
    headers = _headers()
    assert client.post("/api/v1/admin/init", headers=headers).status_code == 200
    root = _create_kb(client, "docs")

    nested = _zip_bytes([("deep/guide.md", b"guide")])
    archive = _zip_bytes(
        [
            ("root.md", b"root"),
            ("packs/manuals.zip", nested),
        ]
    )
    _upload_zip(client, "release.zip", archive)

    root_browse = client.get("/api/v1/kbs/docs/browse", headers=headers)
    assert root_browse.status_code == 200, root_browse.text
    root_payload = root_browse.json()
    assert root_payload["context"] == {
        "kind": "folder",
        "id": root["id"],
        "name": "DOCS",
        "relative_path": "",
        "parent_id": None,
        "parent_folder_id": None,
        "archive_entry_id": None,
    }
    assert root_payload["parent"] is None
    outer = next(entry for entry in root_payload["entries"] if entry["kind"] == "zip")
    assert outer["name"] == "release.zip"
    assert outer["relative_path"] == "release.zip"
    assert outer["parent_id"] is None
    assert outer["parent_folder_id"] == root["id"]

    outer_browse = client.get(
        "/api/v1/kbs/docs/browse",
        params={"archive_entry_id": outer["id"]},
        headers=headers,
    )
    assert outer_browse.status_code == 200, outer_browse.text
    outer_payload = outer_browse.json()
    assert outer_payload["context"]["kind"] == "zip"
    assert outer_payload["context"]["id"] == outer["id"]
    assert outer_payload["parent"]["id"] == root["id"]
    assert {entry["name"] for entry in outer_payload["entries"]} == {
        "packs",
        "root.md",
    }
    packs = next(entry for entry in outer_payload["entries"] if entry["name"] == "packs")

    packs_browse = client.get(
        "/api/v1/kbs/docs/browse",
        params={"archive_entry_id": packs["id"]},
        headers=headers,
    )
    manuals = next(
        entry for entry in packs_browse.json()["entries"] if entry["kind"] == "zip"
    )
    assert manuals["relative_path"] == "packs/manuals.zip"
    assert manuals["parent_id"] == packs["id"]

    manuals_browse = client.get(
        "/api/v1/kbs/docs/browse",
        params={"archive_entry_id": manuals["id"]},
        headers=headers,
    )
    deep = next(entry for entry in manuals_browse.json()["entries"] if entry["kind"] == "folder")
    assert deep["relative_path"] == "packs/manuals.zip/deep"
    assert manuals_browse.json()["parent"]["id"] == packs["id"]

    deep_browse = client.get(
        "/api/v1/kbs/docs/browse",
        params={"archive_entry_id": deep["id"]},
        headers=headers,
    )
    document = next(entry for entry in deep_browse.json()["entries"] if entry["kind"] == "document")
    assert document["slug"] == "guide"
    assert document["title"] == "guide"
    assert document["name"] == "guide.md"
    assert document["original_filename"] == "packs/manuals.zip/deep/guide.md"
    assert document["version"] == 1
    assert document["version_no"] == 1
    assert document["sync_status"] == "not_synced"
    assert document["doc_id"] == document["id"]
    assert document["archive_entry_id"] is not None
    assert document["parent_id"] == deep["id"]
    assert deep_browse.json()["context"]["parent_id"] == manuals["id"]


def test_browse_rejects_conflicting_and_cross_kb_contexts(wm_paths) -> None:
    client = _client(wm_paths)
    headers = _headers()
    assert client.post("/api/v1/admin/init", headers=headers).status_code == 200
    root_docs = _create_kb(client, "docs")
    root_other = _create_kb(client, "other")

    archive = _zip_bytes([("root.md", b"root")])
    _upload_zip(client, "release.zip", archive)
    root_browse = client.get("/api/v1/kbs/docs/browse", headers=headers).json()
    outer = next(entry for entry in root_browse["entries"] if entry["kind"] == "zip")
    document = next(entry for entry in client.get(
        "/api/v1/kbs/docs/browse",
        params={"archive_entry_id": outer["id"]},
        headers=headers,
    ).json()["entries"] if entry["kind"] == "document")

    conflict = client.get(
        "/api/v1/kbs/docs/browse",
        params={"folder_id": root_docs["id"], "archive_entry_id": outer["id"]},
        headers=headers,
    )
    assert conflict.status_code == 400

    wrong_folder = client.get(
        "/api/v1/kbs/docs/browse",
        params={"folder_id": root_other["id"]},
        headers=headers,
    )
    assert wrong_folder.status_code == 404

    wrong_archive = client.get(
        "/api/v1/kbs/other/browse",
        params={"archive_entry_id": outer["id"]},
        headers=headers,
    )
    assert wrong_archive.status_code == 404

    missing_archive = client.get(
        "/api/v1/kbs/docs/browse",
        params={"archive_entry_id": 999999},
        headers=headers,
    )
    assert missing_archive.status_code == 404

    non_container = client.get(
        "/api/v1/kbs/docs/browse",
        params={"archive_entry_id": document["archive_entry_id"]},
        headers=headers,
    )
    assert non_container.status_code == 400


def test_broken_inner_zip_returns_chinese_detail_and_no_state(wm_paths) -> None:
    client = _client(wm_paths)
    headers = _headers()
    assert client.post("/api/v1/admin/init", headers=headers).status_code == 200
    _create_kb(client, "docs")

    broken = _zip_bytes([("broken.zip", b"not a zip")])
    response = client.post(
        "/api/v1/docs",
        data={"kb": ["docs"], "later": "true"},
        files={"file": ("release.zip", broken, "application/zip")},
        headers=headers,
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "broken.zip" in detail
    assert "release.zip" in detail
    assert "内层 ZIP" in detail
    assert "解压失败" in detail
    assert "invalid zip archive" not in detail
    assert client.get("/api/v1/docs", params={"kb": "docs"}, headers=headers).json() == []
    assert client.get("/api/v1/kbs/docs/browse", headers=headers).json()["entries"] == []
