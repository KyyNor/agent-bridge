from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import io
from pathlib import Path
import threading
import time
import zipfile

import pytest

from agent_bridge.core.config import BackendConfig, AgentBridgePaths, ensure_directories
from agent_bridge.core.domain import (
    AccessDenied,
    BackendCapabilities,
    ConflictError,
    NotFound,
    SyncStateStatus,
    ValidationError,
)
from agent_bridge.knowledge_management.docs_knowledge.backends.registry import BackendRegistry
from agent_bridge.app.service import AgentBridgeService


def _service_with_mock_backend(
    wm_paths: AgentBridgePaths, tmp_path: Path | None = None
) -> AgentBridgeService:
    ensure_directories(wm_paths)
    service = AgentBridgeService.create(wm_paths, admins={"root"})
    service.registry = BackendRegistry(
        {"mock": BackendConfig(slug="mock", backend_type="mock")},
        paths=tmp_path or wm_paths.root,
    )
    service.init_system()
    return service


def test_admin_creates_kb_and_member_roles_are_disabled(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    kb = service.create_kb(actor="root", slug="frontend-docs", name="Frontend Docs", description="")
    assert kb["slug"] == "frontend-docs"
    assert service.list_kbs(actor="root")[0]["slug"] == "frontend-docs"
    with pytest.raises(ValidationError, match="member roles are no longer supported"):
        service.grant_kb_member(actor="root", kb_slug="frontend-docs", linux_user="alice", role="contributor")


def test_service_manages_folders_and_rejects_cross_kb_or_cyclic_moves(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "kb-a", "KB A", "")
    service.create_kb("root", "kb-b", "KB B", "")

    root = service.list_folders("root", "kb-a")[0]
    folder = service.create_folder("root", "kb-a", name="Guides", parent_folder_id=root["id"])
    child = service.create_folder("root", "kb-a", name="API", parent_folder_id=folder["id"])
    with pytest.raises(ValidationError):
        service.update_folder("root", "kb-a", folder["id"], parent_folder_id=child["id"])
    renamed = service.update_folder("root", "kb-a", folder["id"], name="Documentation")
    assert renamed["name"] == "Documentation"
    moved = service.update_folder("root", "kb-a", child["id"], parent_folder_id=root["id"])
    assert moved["parent_id"] == root["id"]

    other_root = service.list_folders("root", "kb-b")[0]
    with pytest.raises(NotFound):
        service.create_folder("root", "kb-a", name="Wrong KB", parent_folder_id=other_root["id"])
    with pytest.raises(NotFound):
        service.update_folder("root", "kb-a", folder["id"], parent_folder_id=other_root["id"])
    with pytest.raises(ValidationError):
        service.update_folder("root", "kb-a", root["id"], name="Cannot rename root")
    with pytest.raises(ValidationError):
        service.delete_folder("root", "kb-a", root["id"], confirm=True)


def test_service_scoped_document_delete_preserves_shared_documents(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    kb_a = service.create_kb("root", "kb-a", "KB A", "")
    kb_b = service.create_kb("root", "kb-b", "KB B", "")
    source = tmp_path / "Shared.md"
    source.write_bytes(b"shared")
    doc = service.add_document("root", source, ["kb-a", "kb-b"], later=False)

    result = service.remove_document_from_kb("root", "kb-a", doc["slug"])
    assert result["status"] == "deleted"
    assert service.list_docs("root", "kb-a") == []
    assert service.list_docs("root", "kb-b")[0]["slug"] == doc["slug"]
    assert service.store.get_document_by_slug(doc["slug"])["status"] == "active"
    jobs = service.status("root")["jobs"]
    assert any(job["operation"] == "delete" and job["kb_id"] == kb_a["id"] for job in jobs)

    service.remove_document_from_kb("root", "kb-b", doc["slug"])
    assert service.store.get_document_by_slug(doc["slug"]) is None
    deleted = service.store.get_document_by_slug(doc["slug"], include_deleted=True)
    assert deleted["status"] == "deleted"
    assert any(job["operation"] == "delete" and job["kb_id"] == kb_b["id"] for job in service.status("root")["jobs"])


def test_document_detail_only_returns_visible_kb_placements(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.access.upsert_group(actor="root", group_key="team-a", name="A 组")
    service.access.upsert_group(actor="root", group_key="team-b", name="B 组")
    service.access.set_user_group(actor="root", user_id="alice", group_key="team-a")
    service.access.set_user_group(actor="root", user_id="bob", group_key="team-b")
    shared_kb = service.create_kb("bob", "shared-kb", "共享知识库", "", visibility="shared")
    private_kb = service.create_kb("bob", "private-kb", "B 组知识库", "", visibility="group")
    source = tmp_path / "mixed-scope.md"
    source.write_text("mixed scope", encoding="utf-8")
    doc = service.add_document("bob", source, ["shared-kb", "private-kb"], later=True)
    for kb in (shared_kb, private_kb):
        service.store.upsert_sync_state(
            doc["id"],
            kb["id"],
            "mock",
            f"remote-{kb['slug']}",
            SyncStateStatus.synced,
        )

    visible = service.get_doc("alice", doc["slug"])

    assert visible["kb_slugs"] == ["shared-kb"]
    assert [item["slug"] for item in visible["kbs"]] == ["shared-kb"]
    assert {item["kb_id"] for item in visible["sync_states"]} == {shared_kb["id"]}

    requested = service.get_doc_for_kb("root", "shared-kb", doc["slug"])
    assert requested["kb_slugs"] == ["shared-kb"]
    assert {item["kb_id"] for item in requested["sync_states"]} == {shared_kb["id"]}


def test_service_folder_delete_requires_confirmation_and_recursively_detaches_docs(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "kb-a", "KB A", "")
    service.create_kb("root", "kb-b", "KB B", "")
    root = service.list_folders("root", "kb-a")[0]
    parent = service.create_folder("root", "kb-a", name="Parent", parent_folder_id=root["id"])
    child = service.create_folder("root", "kb-a", name="Child", parent_folder_id=parent["id"])
    source = tmp_path / "Guide.md"
    source.write_bytes(b"guide")
    doc = service.add_document("root", source, ["kb-a", "kb-b"], later=True)
    service.place_document("root", doc["slug"], "kb-a", parent["id"])
    service.store.update_document_placement(doc["id"], service.store.get_kb_by_slug("kb-a")["id"], child["id"])

    preview = service.delete_folder("root", "kb-a", parent["id"], confirm=False)
    assert preview["requires_confirmation"] is True
    assert preview["directory_count"] == 2
    assert preview["file_count"] == 1
    assert service.store.get_folder(service.store.get_kb_by_slug("kb-a")["id"], parent["id"]) is not None

    result = service.delete_folder("root", "kb-a", parent["id"], confirm=True)
    assert result["directory_count"] == 2
    assert result["file_count"] == 1
    assert service.list_docs("root", "kb-a") == []
    assert service.list_docs("root", "kb-b")[0]["slug"] == doc["slug"]
    assert service.store.get_document_by_slug(doc["slug"])["status"] == "active"


def test_get_doc_for_kb_hides_removed_document_association(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "kb-a", "KB A", "")
    service.create_kb("root", "kb-b", "KB B", "")
    source = tmp_path / "Shared.md"
    source.write_bytes(b"shared")
    doc = service.add_document("root", source, ["kb-a", "kb-b"], later=True)

    service.remove_document_from_kb("root", "kb-a", doc["slug"])

    with pytest.raises(NotFound):
        service.get_doc_for_kb("root", "kb-a", doc["slug"])
    visible = service.get_doc_for_kb("root", "kb-b", doc["slug"])
    assert [item["slug"] for item in visible["kbs"]] == ["kb-b"]


def test_update_document_ignores_removed_kb_placements(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    kb_a = service.create_kb("root", "kb-a", "KB A", "")
    kb_b = service.create_kb("root", "kb-b", "KB B", "")
    source = tmp_path / "Guide.md"
    source.write_bytes(b"v1")
    doc = service.add_document("root", source, ["kb-a", "kb-b"], later=True)
    service.remove_document_from_kb("root", "kb-a", doc["slug"])
    existing_job_ids = {job["id"] for job in service.status("root")["jobs"]}

    updated_source = tmp_path / "Guide-v2.md"
    updated_source.write_bytes(b"v2")
    service.update_document("root", doc["slug"], updated_source, later=True)

    new_jobs = [
        job for job in service.status("root")["jobs"] if job["id"] not in existing_job_ids
    ]
    assert {(job["kb_id"], job["operation"]) for job in new_jobs} == {
        (kb_b["id"], "update"),
    }
    assert not any(job["kb_id"] == kb_a["id"] for job in new_jobs)


def test_update_folder_name_and_parent_is_atomic(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "kb", "KB", "")
    root = service.list_folders("root", "kb")[0]
    source = service.create_folder("root", "kb", "Source", parent_folder_id=root["id"])
    target = service.create_folder("root", "kb", "Target", parent_folder_id=root["id"])
    service.create_folder("root", "kb", "Collision", parent_folder_id=target["id"])

    with pytest.raises(ConflictError):
        service.update_folder(
            "root",
            "kb",
            source["id"],
            name="Collision",
            parent_folder_id=target["id"],
        )

    unchanged = service.store.get_folder(service.store.get_kb_by_slug("kb")["id"], source["id"])
    assert unchanged["name"] == "Source"
    assert unchanged["parent_id"] == root["id"]


def test_non_admin_cannot_create_kb(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    with pytest.raises(AccessDenied):
        service.create_kb(actor="alice", slug="frontend-docs", name="Frontend Docs", description="")


def test_admin_adds_doc_to_multiple_kbs(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    service.create_kb("root", "backend-docs", "Backend Docs", "")
    source = tmp_path / "接口说明.pdf"
    source.write_bytes(b"version one")
    doc = service.add_document(actor="root", source=source, kb_slugs=["frontend-docs", "backend-docs"], later=True)
    assert doc["slug"] == "接口说明"
    assert doc["current_version_no"] == 1
    assert len(service.list_docs(actor="root", kb_slug="frontend-docs")) == 1
    assert len(service.list_docs(actor="root", kb_slug="backend-docs")) == 1


def test_duplicate_content_in_same_kb_is_skipped(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "kb", "KB", "")
    first_source = tmp_path / "guide.md"
    second_source = tmp_path / "copy.md"
    first_source.write_bytes(b"same content")
    second_source.write_bytes(b"same content")

    service.add_document("root", first_source, ["kb"], later=True)
    second = service.add_document("root", second_source, ["kb"], later=True)

    assert second["skipped"] is True
    assert second["skip_reason"] == "duplicate_content"
    assert len(service.list_docs("root", "kb")) == 1
    assert len(service.store.list_pending_jobs()) == 1


def test_same_filename_with_different_content_keeps_unique_slug(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "kb", "KB", "")
    first_source = tmp_path / "guide.md"
    second_source = tmp_path / "other.md"
    first_source.write_bytes(b"one")
    second_source.write_bytes(b"two")

    first = service.add_document("root", first_source, ["kb"], later=True)
    second = service.add_document(
        "root", second_source, ["kb"], later=True, original_filename="guide.md"
    )

    assert [first["slug"], second["slug"]] == ["guide", "guide-2"]
    assert len(service.list_docs("root", "kb")) == 2


def test_duplicate_content_is_scoped_to_the_target_kb(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "kb-a", "KB A", "")
    service.create_kb("root", "kb-b", "KB B", "")
    source = tmp_path / "guide.md"
    source.write_bytes(b"same content")

    first = service.add_document("root", source, ["kb-a"], later=True)
    second = service.add_document("root", source, ["kb-b"], later=True)

    assert first.get("skipped") is not True
    assert second.get("skipped") is not True
    assert len(service.list_docs("root", "kb-a")) == 1
    assert len(service.list_docs("root", "kb-b")) == 1


def test_zip_imports_supported_nested_documents_and_skips_duplicate_content(
    wm_paths, tmp_path: Path
) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "kb", "KB", "")
    archive = tmp_path / "docs.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("root.md", b"one")
        zf.writestr("nested/guide.pdf", b"two")
        zf.writestr("nested/copy.txt", b"one")
        zf.writestr("image.png", b"ignored")

    result = service.add_document("root", archive, ["kb"], later=True)

    assert result["uploaded_count"] == 2
    assert result["skipped_count"] == 1
    docs = {doc["slug"]: doc for doc in service.list_docs("root", "kb")}
    assert set(docs) == {"copy", "guide"}
    assert docs["guide"]["folder_path"] == ""
    assert service.store.list_versions(service.store.get_document_by_slug("guide")["id"])[0]["original_filename"] == "nested/guide.pdf"


def test_recursive_zip_import_creates_archive_tree_without_virtual_placements(
    wm_paths, tmp_path: Path
) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "kb", "KB", "")
    nested_zip = io.BytesIO()
    with zipfile.ZipFile(nested_zip, "w") as inner:
        inner.writestr("deep/guide.md", b"deep guide")

    archive = tmp_path / "docs.zip"
    with zipfile.ZipFile(archive, "w") as outer:
        outer.writestr("root.md", b"root guide")
        outer.writestr("packs/manuals.zip", nested_zip.getvalue())

    result = service.add_document("root", archive, ["kb"], later=True)

    assert result["uploaded_count"] == 2
    assert result["skipped_count"] == 0
    docs = {item["slug"]: item for item in service.list_docs("root", "kb")}
    assert {item["folder_path"] for item in docs.values()} == {""}
    assert len(service.status("root")["jobs"]) == 2
    assert all(job["status"] == "pending" for job in service.status("root")["jobs"])

    entries = service.list_archive_entries("root", "kb")
    by_path = {entry["relative_path"]: entry for entry in entries}
    assert by_path["docs.zip"]["kind"] == "zip"
    assert by_path["packs"]["kind"] == "folder"
    assert by_path["packs/manuals.zip"]["kind"] == "zip"
    assert by_path["packs/manuals.zip/deep"]["kind"] == "folder"
    assert by_path["packs/manuals.zip/deep/guide.md"]["kind"] == "document"
    assert by_path["root.md"]["kind"] == "document"
    assert by_path["packs/manuals.zip"]["parent_id"] == by_path["packs"]["id"]
    assert by_path["packs/manuals.zip/deep"]["parent_id"] == by_path["packs/manuals.zip"]["id"]

    guide = docs["guide"]
    guide_version = service.store.list_versions(guide["id"])[0]
    assert guide_version["original_filename"] == "packs/manuals.zip/deep/guide.md"
    assert by_path[guide_version["original_filename"]]["doc_id"] == guide["id"]
    assert guide["archive_entry_id"] == by_path[guide_version["original_filename"]]["id"]


def test_corrupt_inner_zip_rolls_back_all_service_state(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "kb", "KB", "")
    archive = tmp_path / "broken-inner.zip"
    with zipfile.ZipFile(archive, "w") as outer:
        outer.writestr("nested/broken.zip", b"not a zip")

    with pytest.raises(ValidationError):
        service.add_document("root", archive, ["kb"], later=True)

    assert service.list_docs("root", "kb") == []
    assert service.list_archive_entries("root", "kb") == []
    assert service.status("root")["jobs"] == []


def test_zip_duplicate_preserves_real_placement_and_existing_archive_file(
    wm_paths, tmp_path: Path
) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "kb", "KB", "")
    root = service.list_folders("root", "kb")[0]
    first_folder = service.create_folder("root", "kb", "First", root["id"])
    second_folder = service.create_folder("root", "kb", "Second", root["id"])
    source = tmp_path / "first.zip"
    with zipfile.ZipFile(source, "w") as outer:
        outer.writestr("guide.md", b"same content")
    first_result = service.add_document(
        "root", source, ["kb"], later=True, folder_id=first_folder["id"]
    )
    first = first_result["documents"][0]
    first_archive_path = Path(service.store.list_versions(first["id"])[0]["archive_path"])
    original_entry = next(
        entry
        for entry in service.list_archive_entries("root", "kb")
        if entry["kind"] == "document" and entry["relative_path"] == "guide.md"
    )

    archive = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(archive, "w") as outer:
        outer.writestr("nested/guide.md", b"same content")

    result = service.add_document(
        "root", archive, ["kb"], later=True, folder_id=second_folder["id"]
    )

    assert result["uploaded_count"] == 0
    assert result["skipped_count"] == 1
    assert result["skipped"][0]["id"] == first["id"]
    assert len(service.store.list_versions(first["id"])) == 1
    placement = service.store.get_document_placement(
        first["id"], service.store.get_kb_by_slug("kb")["id"]
    )
    assert placement["folder_path"] == "First"
    assert placement["archive_entry_id"] == original_entry["id"]
    assert service.list_docs("root", "kb", folder_id=first_folder["id"])[0]["id"] == first["id"]
    assert first_archive_path.exists()
    assert {folder["name"] for folder in service.list_folders("root", "kb")} == {
        "KB", "First", "Second"
    }
    document_entry = next(
        entry
        for entry in service.list_archive_entries("root", "kb")
        if entry["relative_path"] == "nested/guide.md"
    )
    assert document_entry["doc_id"] == first["id"]
    duplicate_outer = next(
        entry
        for entry in service.list_archive_entries("root", "kb")
        if entry["name"] == "duplicate.zip"
    )
    archive_view = service.browse_kb(
        "root", "kb", archive_entry_id=duplicate_outer["id"]
    )
    nested_view = service.browse_kb(
        "root", "kb", archive_entry_id=document_entry["parent_id"]
    )
    assert any(
        entry["kind"] == "folder" and entry["archive_entry_id"] == document_entry["parent_id"]
        for entry in archive_view["entries"]
    )
    assert any(
        entry["kind"] == "document"
        and entry["doc_id"] == first["id"]
        and entry["archive_entry_id"] == document_entry["id"]
        for entry in nested_view["entries"]
    )
    assert len(service.status("root")["jobs"]) == 1


def test_zip_transaction_removes_new_archive_file_on_rollback(
    wm_paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "kb", "KB", "")
    archive = tmp_path / "rollback.zip"
    with zipfile.ZipFile(archive, "w") as outer:
        outer.writestr("guide.md", b"transactional content")
    before = {
        path
        for path in service.paths.archive_dir.rglob("*")
        if path.is_file()
    }

    def fail_archive_link(*args, **kwargs):
        raise RuntimeError("forced archive link failure")

    monkeypatch.setattr(service.store, "update_archive_entry_document", fail_archive_link)
    with pytest.raises(RuntimeError, match="forced archive link failure"):
        service.add_document("root", archive, ["kb"], later=True)

    after = {
        path
        for path in service.paths.archive_dir.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert service.list_docs("root", "kb") == []
    assert service.list_archive_entries("root", "kb") == []
    assert service.status("root")["jobs"] == []


def test_concurrent_same_content_uploads_are_serialized_and_deduplicated(
    wm_paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "kb", "KB", "")
    sources = []
    for index in range(3):
        source = tmp_path / f"guide-{index}.md"
        source.write_bytes(b"same concurrent content")
        sources.append(source)

    active_hash_checks = 0
    max_active_hash_checks = 0
    counters_lock = threading.Lock()
    original_find = service.store.find_current_document_by_content_hash

    def tracked_find(kb_id: int, content_hash: str):
        nonlocal active_hash_checks, max_active_hash_checks
        with counters_lock:
            active_hash_checks += 1
            max_active_hash_checks = max(max_active_hash_checks, active_hash_checks)
        try:
            time.sleep(0.02)
            return original_find(kb_id, content_hash)
        finally:
            with counters_lock:
                active_hash_checks -= 1

    monkeypatch.setattr(service.store, "find_current_document_by_content_hash", tracked_find)
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(
            executor.map(
                lambda source: service.add_document("root", source, ["kb"], later=True),
                sources,
            )
        )

    assert max_active_hash_checks == 1
    assert len(service.list_docs("root", "kb")) == 1
    assert sum(bool(result.get("skipped")) for result in results) == 2


def test_concurrent_zip_rollback_does_not_remove_successful_archive(
    wm_paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "kb", "KB", "")
    failing_archive = tmp_path / "failing.zip"
    successful_archive = tmp_path / "successful.zip"
    with zipfile.ZipFile(failing_archive, "w") as archive:
        archive.writestr("guide.md", b"failing upload")
    with zipfile.ZipFile(successful_archive, "w") as archive:
        archive.writestr("guide.md", b"successful upload")

    active_snapshots = 0
    max_active_snapshots = 0
    snapshot_lock = threading.Lock()
    original_archive_files = service._archive_files

    def tracked_archive_files() -> set[Path]:
        nonlocal active_snapshots, max_active_snapshots
        with snapshot_lock:
            active_snapshots += 1
            max_active_snapshots = max(max_active_snapshots, active_snapshots)
        try:
            time.sleep(0.02)
            return original_archive_files()
        finally:
            with snapshot_lock:
                active_snapshots -= 1

    monkeypatch.setattr(service, "_archive_files", tracked_archive_files)
    original_update = service.store.update_archive_entry_document
    upload_state = threading.local()

    def fail_one_archive_entry(entry_id: int, doc_id: int) -> None:
        if getattr(upload_state, "should_fail", False):
            raise RuntimeError("forced concurrent rollback")
        original_update(entry_id, doc_id)

    monkeypatch.setattr(service.store, "update_archive_entry_document", fail_one_archive_entry)

    def upload(source: Path, should_fail: bool):
        upload_state.should_fail = should_fail
        try:
            return service.add_document("root", source, ["kb"], later=True)
        finally:
            del upload_state.should_fail

    with ThreadPoolExecutor(max_workers=2) as executor:
        failed = executor.submit(upload, failing_archive, True)
        successful = executor.submit(upload, successful_archive, False)
        with pytest.raises(RuntimeError, match="forced concurrent rollback"):
            failed.result()
        result = successful.result()

    assert max_active_snapshots == 1
    assert result["uploaded_count"] == 1
    assert len(service.list_docs("root", "kb")) == 1
    document = result["documents"][0]
    archive_path = Path(service.store.list_versions(document["id"])[0]["archive_path"])
    assert archive_path.exists()
    assert {
        path
        for path in service.paths.archive_dir.rglob("*")
        if path.is_file()
    } == {archive_path}


def test_document_relative_path_uses_selected_folder_as_base(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "kb", "KB", "")
    root = service.list_folders("root", "kb")[0]
    base = service.create_folder("root", "kb", "Base", parent_folder_id=root["id"])
    source = tmp_path / "guide.md"
    source.write_bytes(b"guide")

    doc = service.add_document(
        "root", source, ["kb"], later=True, folder_id=base["id"], relative_path=r"A\\B\\guide.md"
    )

    version = service.store.list_versions(doc["id"])[0]
    placement = service.store.get_document_placement(doc["id"], service.store.get_kb_by_slug("kb")["id"])
    assert version["original_filename"] == "A/B/guide.md"
    assert doc["title"] == "guide"
    assert placement["folder_path"] == "Base/A/B"
    assert not placement["folder_path"].startswith("root/")


def test_multi_kb_relative_path_creates_same_subfolders_without_explicit_folder(
    wm_paths, tmp_path: Path
) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "kb-a", "KB A", "")
    service.create_kb("root", "kb-b", "KB B", "")
    source = tmp_path / "guide.md"
    source.write_bytes(b"guide")

    doc = service.add_document(
        "root", source, ["kb-a", "kb-b"], later=True, relative_path="shared/docs/guide.md"
    )

    for kb_slug in ("kb-a", "kb-b"):
        kb = service.store.get_kb_by_slug(kb_slug)
        placement = service.store.get_document_placement(doc["id"], kb["id"])
        assert placement["folder_path"] == "shared/docs"
        assert service.list_docs("root", kb_slug, folder_id=placement["folder_id"])[0]["slug"] == doc["slug"]


def test_explicit_folder_is_rejected_for_multiple_kbs(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "kb-a", "KB A", "")
    service.create_kb("root", "kb-b", "KB B", "")
    root = service.list_folders("root", "kb-a")[0]
    source = tmp_path / "guide.md"
    source.write_bytes(b"guide")

    with pytest.raises(ValidationError, match="folder_id can only be used"):
        service.add_document("root", source, ["kb-a", "kb-b"], later=True, folder_id=root["id"])


def test_duplicate_content_does_not_move_existing_placement(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "kb", "KB", "")
    root = service.list_folders("root", "kb")[0]
    first_folder = service.create_folder("root", "kb", "First", parent_folder_id=root["id"])
    second_folder = service.create_folder("root", "kb", "Second", parent_folder_id=root["id"])
    source = tmp_path / "guide.md"
    source.write_bytes(b"same")

    first = service.add_document(
        "root", source, ["kb"], later=True, folder_id=first_folder["id"], relative_path="guide.md"
    )
    duplicate = service.add_document(
        "root", source, ["kb"], later=True, folder_id=second_folder["id"], relative_path="other/guide.md"
    )

    placement = service.store.get_document_placement(first["id"], service.store.get_kb_by_slug("kb")["id"])
    assert duplicate["skipped"] is True
    assert placement["folder_path"] == "First"


def test_git_sync_uses_repository_relative_path_without_root_prefix(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "kb", "KB", "")
    repo_path = tmp_path / "repo"
    (repo_path / "A" / "B").mkdir(parents=True)
    (repo_path / "root.md").write_bytes(b"root")
    (repo_path / "A" / "B" / "file.md").write_bytes(b"nested")
    service.store.upsert_code_repository(
        repo_key="docs-repo", name="Docs Repo", git_url="", branch="main", auth_ref="",
        description="", tags=[], category_key="", sync_interval_minutes=60,
        auto_understand=False, status="active",
    )
    service.store.mark_code_repository_sync(
        "docs-repo", local_path=str(repo_path), last_commit="test", success=True, error=None
    )
    service.store.upsert_kb_repo_source(service.store.get_kb_by_slug("kb")["id"], "docs-repo", [".md"])
    service.codegraph.sync_repository = lambda actor, repo_key: None

    result = service.sync_kb_repo_source("root", "kb", "docs-repo")

    assert result["added"] == 2
    documents = {item["title"]: item for item in service.list_docs("root", "kb")}
    assert documents["root"]["folder_path"] == ""
    assert documents["file"]["folder_path"] == "A/B"
    assert all(not (item["folder_path"] or "").startswith("root/") for item in documents.values())


def test_zip_rejects_malformed_archive_without_creating_documents(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "kb", "KB", "")
    archive = tmp_path / "broken.zip"
    archive.write_bytes(b"not a zip")

    with pytest.raises(ValidationError, match="压缩包解压失败") as error:
        service.add_document("root", archive, ["kb"], later=True)

    assert "invalid zip archive" not in str(error.value)
    assert service.list_docs("root", "kb") == []


def test_zip_rejects_path_traversal_and_empty_archives(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "kb", "KB", "")
    traversal = tmp_path / "traversal.zip"
    with zipfile.ZipFile(traversal, "w") as zf:
        zf.writestr("../escape.md", b"escape")

    with pytest.raises(ValidationError, match="压缩包成员路径不安全"):
        service.add_document("root", traversal, ["kb"], later=True)

    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as zf:
        zf.writestr("image.png", b"ignored")

    with pytest.raises(ValidationError, match="压缩包中没有支持的文档"):
        service.add_document("root", empty, ["kb"], later=True)

    assert service.list_docs("root", "kb") == []


def test_update_document_creates_new_version(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    v1 = tmp_path / "Guide.pdf"
    v2 = tmp_path / "Guide-v2.pdf"
    v1.write_bytes(b"one")
    v2.write_bytes(b"two")
    doc = service.add_document("root", v1, ["frontend-docs"], later=True)
    updated = service.update_document("root", doc["slug"], v2, later=True)
    assert updated["current_version_no"] == 2


def test_non_admin_cannot_add_document(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"one")
    with pytest.raises(AccessDenied):
        service.add_document("alice", source, ["frontend-docs"], later=True)


def test_invisible_document_edits_return_not_found(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    v1 = tmp_path / "Guide.pdf"
    v2 = tmp_path / "Guide-v2.pdf"
    v1.write_bytes(b"one")
    v2.write_bytes(b"two")
    doc = service.add_document("root", v1, ["frontend-docs"], later=True)

    with pytest.raises(AccessDenied):
        service.update_document("alice", doc["slug"], v2, later=True)
    with pytest.raises(AccessDenied):
        service.delete_document("alice", doc["slug"])
    with pytest.raises(AccessDenied):
        service.purge_document("alice", doc["slug"], confirm=True)


def test_admin_can_update_shared_document(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "kb-a", "KB A", "")
    service.create_kb("root", "kb-b", "KB B", "")
    v1 = tmp_path / "Guide.pdf"
    v2 = tmp_path / "Guide-v2.pdf"
    v1.write_bytes(b"one")
    v2.write_bytes(b"two")
    doc = service.add_document("root", v1, ["kb-a", "kb-b"], later=True)

    updated = service.update_document("root", doc["slug"], v2, later=True)
    assert updated["current_version_no"] == 2


def test_purge_requires_confirmation(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"one")
    doc = service.add_document("root", source, ["frontend-docs"], later=True)

    with pytest.raises(ValidationError, match="purge requires confirmation"):
        service.purge_document("root", doc["slug"])


def test_duplicate_content_upload_reuses_existing_document_and_archive(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    first = tmp_path / "Guide-A.pdf"
    second = tmp_path / "Guide-B.pdf"
    first.write_bytes(b"same bytes")
    second.write_bytes(b"same bytes")
    doc_a = service.add_document("root", first, ["frontend-docs"], later=True)
    doc_b = service.add_document("root", second, ["frontend-docs"], later=True)
    version_a = service.store.list_versions(doc_a["id"])[0]
    archive_path = Path(version_a["archive_path"])

    assert archive_path.exists()
    assert doc_b["id"] == doc_a["id"]
    assert doc_b["skipped"] is True
    assert len(service.list_docs("root", "frontend-docs")) == 1

    service.purge_document("root", doc_b["slug"], confirm=True)
    assert not archive_path.exists()


def test_get_doc_does_not_expose_archive_paths_to_admin_response(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"one")
    doc = service.add_document("root", source, ["frontend-docs"], later=True)

    visible = service.get_doc("root", doc["slug"])

    assert visible["versions"]
    assert "archive_path" not in visible["versions"][0]


def test_get_doc_exposes_only_active_kb_placements(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "kb-a", "KB A", "")
    service.create_kb("root", "kb-b", "KB B", "")
    source = tmp_path / "Guide.md"
    source.write_bytes(b"one")
    doc = service.add_document("root", source, ["kb-a", "kb-b"], later=True)

    service.remove_document_from_kb("root", "kb-a", doc["slug"])

    visible = service.get_doc("root", doc["slug"])

    assert [kb["slug"] for kb in visible["kbs"]] == ["kb-b"]
    assert visible["kb_slugs"] == ["kb-b"]


def test_invisible_kb_returns_not_found(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    with pytest.raises(AccessDenied):
        service.list_docs(actor="alice", kb_slug="frontend-docs")


def test_immediate_add_syncs_to_mock_backend(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"one")
    service.add_document("root", source, ["frontend-docs"], later=False)
    status = service.status(actor="root")
    assert status["jobs"][0]["status"] == "succeeded"
    docs = service.list_docs(actor="root", kb_slug="frontend-docs")
    assert docs[0]["sync_status"] == "synced"


def test_list_docs_uses_kb_default_backend_sync_state(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    kb = service.create_kb("root", "docs", "Docs", "")
    service.store.ensure_backend_target(kb["id"], slug="weknora", backend_type="weknora")
    service.update_kb_defaults("root", "docs", default_backend_slug="weknora")

    source = tmp_path / "Guide.md"
    source.write_bytes(b"one")
    doc = service.add_document("root", source, ["docs"], later=True)
    service.store.upsert_sync_state(
        doc_id=doc["id"],
        kb_id=kb["id"],
        backend_slug="weknora",
        backend_doc_id="remote-guide",
        status=SyncStateStatus.synced,
    )

    docs = service.list_docs(actor="root", kb_slug="docs")

    assert docs[0]["sync_status"] == "synced"


def test_list_docs_honors_requested_backend_sync_state(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    kb = service.create_kb("root", "docs", "Docs", "")
    service.store.ensure_backend_target(kb["id"], slug="weknora", backend_type="weknora")

    source = tmp_path / "Guide.md"
    source.write_bytes(b"one")
    doc = service.add_document("root", source, ["docs"], later=True)
    service.store.upsert_sync_state(
        doc_id=doc["id"],
        kb_id=kb["id"],
        backend_slug="weknora",
        backend_doc_id="remote-guide",
        status=SyncStateStatus.synced,
    )

    docs = service.list_docs(actor="root", kb_slug="docs", backend="weknora")

    assert docs[0]["sync_status"] == "synced"


def test_sync_uses_backend_kb_id_for_upload_and_delete(wm_paths, tmp_path: Path) -> None:
    class RecordingBackend:
        def __init__(self) -> None:
            self.upload_kb_ids: list[str] = []
            self.delete_kb_ids: list[str] = []

        def create_kb(self, slug: str, name: str) -> str:
            return "backend-dataset-123"

        def delete_kb(self, backend_kb_id: str) -> None:
            pass

        def capabilities(self) -> BackendCapabilities:
            return BackendCapabilities(supports_folders=False)

        def upload(
            self,
            backend_kb_id: str,
            doc_slug: str,
            file_path: Path,
            filename: str,
            remote_path: str | None = None,
        ) -> str:
            self.upload_kb_ids.append(backend_kb_id)
            return "backend-doc-456"

        def move(
            self,
            backend_kb_id: str,
            backend_doc_id: str,
            file_path: Path,
            filename: str,
            remote_path: str | None = None,
        ) -> str:
            raise NotImplementedError

        def relocate(
            self,
            backend_kb_id: str,
            backend_doc_id: str,
            file_path: Path,
            filename: str,
            remote_path: str | None = None,
        ) -> str:
            return self.move(backend_kb_id, backend_doc_id, file_path, filename, remote_path)

        def delete(self, backend_kb_id: str, backend_doc_id: str) -> None:
            self.delete_kb_ids.append(backend_kb_id)

        def get_status(self, backend_kb_id: str, backend_doc_id: str):
            from agent_bridge.core.domain import BackendDocStatus

            return BackendDocStatus(status="completed")

        def retrieve(self, backend_kb_id: str, question: str, top_k: int = 6):
            return []

        def ask(self, backend_kb_id: str, question: str, chat_id: str | None = None, session_id: str | None = None):
            from agent_bridge.core.domain import AskResult

            return AskResult(answer="", chunks=[], session_id=None), ""

    ensure_directories(wm_paths)
    backend = RecordingBackend()
    service = AgentBridgeService.create(wm_paths, admins={"root"})
    service.registry = BackendRegistry(
        {"custom": BackendConfig(slug="custom", backend_type="mock")},
        paths=tmp_path,
    )
    service.registry._adapters["custom"] = backend  # type: ignore[attr-defined]
    service.init_system()
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"one")

    doc = service.add_document("root", source, ["frontend-docs"], later=False)
    service.delete_document("root", doc["slug"], later=False)

    assert backend.upload_kb_ids == ["backend-dataset-123"]
    assert backend.delete_kb_ids == ["backend-dataset-123"]


def test_sync_recovers_one_missing_remote_kb_for_all_snapshot_documents(wm_paths, tmp_path: Path) -> None:
    class RecoveringBackend:
        def __init__(self) -> None:
            self.created_kb_ids: list[str] = []
            self.valid_kb_ids: set[str] = set()
            self.upload_kb_ids: list[str] = []

        def create_kb(self, slug: str, name: str) -> str:
            backend_kb_id = f"remote-kb-{len(self.created_kb_ids) + 1}"
            self.created_kb_ids.append(backend_kb_id)
            self.valid_kb_ids.add(backend_kb_id)
            return backend_kb_id

        def delete_kb(self, backend_kb_id: str) -> None:
            self.valid_kb_ids.discard(backend_kb_id)

        def capabilities(self) -> BackendCapabilities:
            return BackendCapabilities(supports_folders=False)

        def upload(
            self,
            backend_kb_id: str,
            doc_slug: str,
            file_path: Path,
            filename: str,
            remote_path: str | None = None,
        ) -> str:
            if backend_kb_id not in self.valid_kb_ids:
                raise RuntimeError("Weknora API error: knowledge base not found (404, code 1003)")
            self.upload_kb_ids.append(backend_kb_id)
            return f"{backend_kb_id}:{doc_slug}"

        def delete(self, backend_kb_id: str, backend_doc_id: str) -> None:
            pass

        def get_status(self, backend_kb_id: str, backend_doc_id: str):
            return None

        def retrieve(self, backend_kb_id: str, question: str, top_k: int = 6):
            return []

        def ask(self, backend_kb_id: str, question: str, chat_id: str | None = None, session_id: str | None = None):
            return None, ""

    ensure_directories(wm_paths)
    backend = RecoveringBackend()
    service = AgentBridgeService.create(wm_paths, admins={"root"})
    service.registry = BackendRegistry(
        {"weknora": BackendConfig(slug="weknora", backend_type="mock")},
        paths=tmp_path,
    )
    service.registry._adapters["weknora"] = backend  # type: ignore[attr-defined]
    service.init_system()
    kb = service.create_kb("root", "docs", "Docs", "")
    old_backend_kb_id = backend.created_kb_ids[-1]
    backend.valid_kb_ids.remove(old_backend_kb_id)

    for filename in ("one.md", "two.md"):
        source = tmp_path / filename
        source.write_text(filename, encoding="utf-8")
        service.add_document("root", source, ["docs"], later=True)

    result = service.sync("root", all_users=False)

    target = next(target for target in service.store.list_backend_targets(kb["id"]) if target["slug"] == "weknora")
    assert result == {"processed": 2, "succeeded": 2, "failed": 0}
    assert backend.created_kb_ids == [old_backend_kb_id, "remote-kb-2"]
    assert backend.upload_kb_ids == ["remote-kb-2", "remote-kb-2"]
    assert target["backend_kb_id"] == "remote-kb-2"


def test_align_backends_repairs_existing_target_with_missing_backend_kb_id(wm_paths, tmp_path: Path) -> None:
    class RecordingBackend:
        def __init__(self) -> None:
            self.created_kb_ids: list[str] = []
            self.upload_kb_ids: list[str] = []

        def create_kb(self, slug: str, name: str) -> str:
            backend_kb_id = f"remote-kb-{len(self.created_kb_ids) + 1}"
            self.created_kb_ids.append(backend_kb_id)
            return backend_kb_id

        def delete_kb(self, backend_kb_id: str) -> None:
            pass

        def capabilities(self) -> BackendCapabilities:
            return BackendCapabilities(supports_folders=False)

        def upload(
            self,
            backend_kb_id: str,
            doc_slug: str,
            file_path: Path,
            filename: str,
            remote_path: str | None = None,
        ) -> str:
            self.upload_kb_ids.append(backend_kb_id)
            return f"{backend_kb_id}:{doc_slug}"

        def delete(self, backend_kb_id: str, backend_doc_id: str) -> None:
            pass

        def get_status(self, backend_kb_id: str, backend_doc_id: str):
            return None

        def retrieve(self, backend_kb_id: str, question: str, top_k: int = 6):
            return []

        def ask(self, backend_kb_id: str, question: str, chat_id: str | None = None, session_id: str | None = None):
            return None, ""

    ensure_directories(wm_paths)
    backend = RecordingBackend()
    service = AgentBridgeService.create(wm_paths, admins={"root"})
    service.registry = BackendRegistry(
        {"weknora": BackendConfig(slug="weknora", backend_type="mock")},
        paths=tmp_path,
    )
    service.registry._adapters["weknora"] = backend  # type: ignore[attr-defined]
    service.init_system()
    kb = service.create_kb("root", "docs", "Docs", "")
    with service.store.connect() as conn:
        conn.execute(
            "UPDATE backend_targets SET backend_kb_id = NULL WHERE kb_id = ? AND slug = ?",
            (kb["id"], "weknora"),
        )

    source = tmp_path / "guide.md"
    source.write_text("guide", encoding="utf-8")
    service.add_document("root", source, ["docs"], later=True)

    service.align_backends()
    service.sync("root", all_users=False)

    target = next(target for target in service.store.list_backend_targets(kb["id"]) if target["slug"] == "weknora")
    assert backend.created_kb_ids == ["remote-kb-1", "remote-kb-2"]
    assert target["backend_kb_id"] == "remote-kb-2"
    assert backend.upload_kb_ids == ["remote-kb-2"]


def test_sync_processes_later_job(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"one")
    service.add_document("root", source, ["frontend-docs"], later=True)
    before = service.status(actor="root")
    assert before["jobs"][0]["status"] == "pending"
    result = service.sync(actor="root", all_users=False)
    assert result["processed"] == 1
    after = service.status(actor="root")
    assert after["jobs"][0]["status"] == "succeeded"


def test_delete_creates_delete_job_and_sync_marks_deleted(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    kb = service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"one")
    doc = service.add_document("root", source, ["frontend-docs"], later=False)
    service.delete_document("root", doc["slug"])

    before = service.status(actor="root")
    assert before["jobs"][-1]["operation"] == "delete"
    assert before["jobs"][-1]["status"] == "pending"

    service.sync(actor="root", all_users=False)

    after = service.status(actor="root")
    assert after["jobs"][-1]["status"] == "succeeded"
    sync_state = service.store.get_sync_state(doc["id"], kb["id"], backend_slug="mock")
    assert sync_state is not None
    assert sync_state["status"] == "deleted"


def test_delete_cancels_unsynced_pending_create_without_delete_job(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"one")
    doc = service.add_document("root", source, ["frontend-docs"], later=True)

    service.delete_document("root", doc["slug"])

    jobs = service.status(actor="root")["jobs"]
    assert [(job["operation"], job["status"]) for job in jobs] == [("create", "cancelled")]
    assert service.sync(actor="root", all_users=False)["processed"] == 0


def test_delete_document_ignores_removed_kb_placements(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    kb_a = service.create_kb("root", "kb-a", "KB A", "")
    kb_b = service.create_kb("root", "kb-b", "KB B", "")
    source = tmp_path / "Guide.md"
    source.write_bytes(b"one")
    doc = service.add_document("root", source, ["kb-a", "kb-b"], later=False)

    service.remove_document_from_kb("root", "kb-a", doc["slug"])
    existing_job_ids = {job["id"] for job in service.status("root")["jobs"]}

    service.delete_document("root", doc["slug"])

    new_jobs = [
        job for job in service.status("root")["jobs"] if job["id"] not in existing_job_ids
    ]
    assert {(job["kb_id"], job["operation"]) for job in new_jobs} == {
        (kb_b["id"], "delete"),
    }
    assert not any(job["kb_id"] == kb_a["id"] for job in new_jobs)


def test_delete_cancels_pending_update_but_keeps_delete_for_synced_doc(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    kb = service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"one")
    doc = service.add_document("root", source, ["frontend-docs"], later=False)
    update = tmp_path / "Guide-v2.pdf"
    update.write_bytes(b"two")
    service.update_document("root", doc["slug"], update, later=True)

    service.delete_document("root", doc["slug"])

    jobs = service.status(actor="root")["jobs"]
    assert [(job["operation"], job["status"]) for job in jobs] == [
        ("create", "succeeded"),
        ("update", "cancelled"),
        ("delete", "pending"),
    ]
    service.sync(actor="root", all_users=False)
    sync_state = service.store.get_sync_state(doc["id"], kb["id"], backend_slug="mock")
    assert sync_state is not None
    assert sync_state["status"] == "deleted"


def test_delete_with_later_false_syncs_immediately(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"one")
    doc = service.add_document("root", source, ["frontend-docs"], later=False)

    service.delete_document("root", doc["slug"], later=False)

    status = service.status(actor="root")
    assert status["jobs"][-1]["operation"] == "delete"
    assert status["jobs"][-1]["status"] == "succeeded"


def test_sync_failure_updates_sync_state(wm_paths, tmp_path: Path, monkeypatch) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    kb = service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"one")
    doc = service.add_document("root", source, ["frontend-docs"], later=True)

    def fail_upsert(*args, **kwargs):
        raise RuntimeError("backend down")

    # Monkeypatch the registry adapter, not mock_backend directly
    mock_adapter = service.registry.get("mock")
    monkeypatch.setattr(mock_adapter, "upload", fail_upsert)
    service.sync("root", all_users=False)

    status = service.status("root")
    assert status["jobs"][0]["status"] == "failed"
    sync_state = service.store.get_sync_state(doc["id"], kb["id"])
    assert sync_state is not None
    assert sync_state["status"] == SyncStateStatus.sync_failed.value


def test_sync_missing_adapter_fails_instead_of_falling_back_to_mock(
    wm_paths, tmp_path: Path
) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    kb = service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"one")
    doc = service.add_document("root", source, ["frontend-docs"], later=True)
    job = service.store.list_runnable_jobs(actor=None)[0]

    service.registry.remove_backend("mock")
    assert service._run_job(job) is False

    stored_job = service.store.list_all_jobs()[0]
    sync_state = service.store.get_sync_state(doc["id"], kb["id"], "mock")
    assert stored_job["status"] == "failed"
    assert "not configured or unavailable" in stored_job["error"]
    assert sync_state["status"] == SyncStateStatus.sync_failed.value


def test_delete_failure_updates_sync_state(wm_paths, tmp_path: Path, monkeypatch) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    kb = service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"one")
    doc = service.add_document("root", source, ["frontend-docs"], later=False)
    service.delete_document("root", doc["slug"])

    def fail_delete(*args, **kwargs):
        raise RuntimeError("backend down")

    mock_adapter = service.registry.get("mock")
    monkeypatch.setattr(mock_adapter, "delete", fail_delete)
    service.sync("root", all_users=False)

    status = service.status("root")
    assert status["jobs"][-1]["status"] == "failed"
    sync_state = service.store.get_sync_state(doc["id"], kb["id"])
    assert sync_state is not None
    assert sync_state["status"] == SyncStateStatus.delete_failed.value


def test_align_backends_reactivates_inactive_target(wm_paths, tmp_path):
    service = _service_with_mock_backend(wm_paths, tmp_path)
    kb = service.create_kb("root", "test-kb", "Test KB", "")
    service.store.set_backend_target_status(kb["id"], "mock", "inactive")
    service.align_backends()
    targets = service.store.list_backend_targets(kb["id"])
    mock_target = next(t for t in targets if t["slug"] == "mock")
    assert mock_target["status"] == "active"


def test_align_backends_marks_removed_backend_inactive(wm_paths, tmp_path):
    service = _service_with_mock_backend(wm_paths, tmp_path)
    kb = service.create_kb("root", "test-kb", "Test KB", "")
    # Manually add a backend target that doesn't exist in registry
    service.store.ensure_backend_target(kb["id"], slug="nonexistent", backend_type="nonexistent")
    service.align_backends()
    targets = service.store.list_backend_targets(kb["id"])
    nonexistent = next(t for t in targets if t["slug"] == "nonexistent")
    assert nonexistent["status"] == "inactive"


def test_align_backends_no_registry_is_noop(wm_paths):
    ensure_directories(wm_paths)
    service = AgentBridgeService.create(wm_paths, admins={"root"})
    service.registry = None
    service.init_system()
    # Should not raise
    service.align_backends()


def test_admin_can_add_pageindex_backend(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)

    row = service.add_backend(
        "root",
        "pageindex-main",
        "pageindex",
        base_url="http://litellm.internal/v1",
        api_key="internal-key",
        embedding_model_id="openai/local-chat",
        summary_model_id="openai/local-chat",
    )

    assert row["slug"] == "pageindex-main"
    assert row["backend_type"] == "pageindex"
    assert row["api_key_set"] is True
    assert row["runtime_status"] == "active"
    assert service.registry is not None
    assert service.registry.get("pageindex-main") is not None


def test_search_with_default_backend(wm_paths, tmp_path: Path, monkeypatch) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    kb = service.create_kb("root", "frontend-docs", "Frontend Docs", "")

    from agent_bridge.core.domain import RetrievalResult
    mock_results = [RetrievalResult(
        chunk_id="c1", content="hello", document_name="a.md",
        similarity=0.9, dataset_id=kb["id"],
    )]
    adapter = service.registry.get("mock")
    monkeypatch.setattr(adapter, "retrieve", lambda *a, **kw: mock_results)

    results = service.search("root", "frontend-docs", "hello")
    assert len(results) == 1
    assert results[0].chunk_id == "c1"


def test_search_with_explicit_backend(wm_paths, tmp_path: Path, monkeypatch) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")

    from agent_bridge.core.domain import RetrievalResult
    adapter = service.registry.get("mock")
    monkeypatch.setattr(adapter, "retrieve", lambda *a, **kw: [])

    results = service.search("root", "frontend-docs", "hello", backend_slug="mock")
    assert results == []


def test_search_kb_not_found(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    with pytest.raises(NotFound):
        service.search("root", "nonexistent", "hello")


def test_search_no_retrieval_backend(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    service.registry = None
    with pytest.raises(NotFound, match="backend 'mock' is not configured or unavailable"):
        service.search("root", "frontend-docs", "hello")


def test_ask_with_default_backend(wm_paths, tmp_path: Path, monkeypatch) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")

    from agent_bridge.core.domain import AskResult
    mock_result = AskResult(answer="yes", chunks=[], session_id="s1")
    adapter = service.registry.get("mock")
    monkeypatch.setattr(adapter, "ask", lambda *a, **kw: (mock_result, ""))

    result = service.ask("root", "frontend-docs", "what is X?")
    assert result.answer == "yes"


def test_ask_persists_chat_id(wm_paths, tmp_path: Path, monkeypatch) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    kb = service.create_kb("root", "frontend-docs", "Frontend Docs", "")

    from agent_bridge.core.domain import AskResult
    mock_result = AskResult(answer="yes", chunks=[], session_id="s1")
    adapter = service.registry.get("mock")
    monkeypatch.setattr(adapter, "ask", lambda *a, **kw: (mock_result, "chat-new"))

    service.ask("root", "frontend-docs", "what is X?", backend_slug="mock")

    targets = service.store.list_backend_targets(kb["id"])
    import json
    mock_target = next(t for t in targets if t["slug"] == "mock")
    config = json.loads(mock_target["config_json"])
    assert config["chat_id"] == "chat-new"


def test_ask_requires_capability_profile_for_non_admin(wm_paths, tmp_path: Path, monkeypatch) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "frontend-docs", "Frontend Docs", "", visibility="shared")

    from agent_bridge.core.domain import AskResult
    mock_result = AskResult(answer="yes", chunks=[], session_id="s1")
    adapter = service.registry.get("mock")
    monkeypatch.setattr(adapter, "ask", lambda *a, **kw: (mock_result, ""))

    service.governance.upsert_profile("root", "safe", "Safe", "", "active")
    service.access.set_user_group(
        actor="root",
        user_id="alice",
        group_key=service.access.maintenance_group_key,
    )

    with pytest.raises(AccessDenied, match="capability profile is required"):
        service.ask("alice", "frontend-docs", "what is X?")

    with pytest.raises(AccessDenied, match="resource is blocked by profile policy"):
        service.ask("alice", "frontend-docs", "what is X?", profile_key="safe")

    service.governance.set_resource_profiles(
        "root",
        "wiki_kb",
        "frontend-docs",
        ["safe"],
    )

    result = service.ask("alice", "frontend-docs", "what is X?", profile_key="safe")
    assert result.answer == "yes"
