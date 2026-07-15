from __future__ import annotations

import zipfile
from pathlib import Path

from agent_bridge.app.service import AgentBridgeService
from agent_bridge.core.config import BackendConfig, ensure_directories
from agent_bridge.core.domain import AskResult, BackendCapabilities, BackendDocStatus
from agent_bridge.knowledge_management.docs_knowledge.backends.registry import BackendRegistry


class RecordingBackend:
    def __init__(self, supports_folders: bool) -> None:
        self._supports_folders = supports_folders
        self.created_kbs: list[tuple[str, str]] = []
        self.uploads: list[dict] = []
        self.moves: list[dict] = []
        self.deletes: list[dict] = []
        self._next_id = 0

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(supports_folders=self._supports_folders)

    def create_kb(self, slug: str, name: str) -> str:
        self.created_kbs.append((slug, name))
        return f"remote-{slug}"

    def delete_kb(self, backend_kb_id: str) -> None:
        return None

    def upload(
        self,
        backend_kb_id: str,
        doc_slug: str,
        file_path: Path,
        filename: str,
        remote_path: str | None = None,
    ) -> str:
        self._next_id += 1
        backend_doc_id = f"remote-doc-{self._next_id}"
        self.uploads.append(
            {
                "backend_kb_id": backend_kb_id,
                "doc_slug": doc_slug,
                "filename": filename,
                "remote_path": remote_path,
                "backend_doc_id": backend_doc_id,
            }
        )
        return backend_doc_id

    def move(
        self,
        backend_kb_id: str,
        backend_doc_id: str,
        file_path: Path,
        filename: str,
        remote_path: str | None = None,
    ) -> str:
        self._next_id += 1
        new_id = f"remote-doc-{self._next_id}"
        self.moves.append(
            {
                "backend_kb_id": backend_kb_id,
                "backend_doc_id": backend_doc_id,
                "filename": filename,
                "remote_path": remote_path,
                "new_backend_doc_id": new_id,
            }
        )
        return new_id

    def relocate(self, *args, **kwargs) -> str:
        return self.move(*args, **kwargs)

    def delete(self, backend_kb_id: str, backend_doc_id: str) -> None:
        self.deletes.append({"backend_kb_id": backend_kb_id, "backend_doc_id": backend_doc_id})

    def get_status(self, backend_kb_id: str, backend_doc_id: str) -> BackendDocStatus:
        return BackendDocStatus(status="completed")

    def retrieve(self, backend_kb_id: str, question: str, top_k: int = 6) -> list:
        return []

    def ask(self, backend_kb_id: str, question: str, chat_id: str | None = None, session_id: str | None = None, agent_id: str | None = None) -> tuple[AskResult, str]:
        return AskResult(answer="", chunks=[], session_id=None), ""


def _service(wm_paths, tmp_path: Path, backends: dict[str, RecordingBackend]) -> AgentBridgeService:
    ensure_directories(wm_paths)
    service = AgentBridgeService.create(wm_paths, admins={"root"})
    service.registry = BackendRegistry(
        {slug: BackendConfig(slug=slug, backend_type="mock") for slug in backends},
        paths=tmp_path,
    )
    service.registry._adapters.update(backends)  # type: ignore[attr-defined]
    service.init_system()
    return service


def test_folder_sync_uses_capability_matrix_and_preserves_root_path(wm_paths, tmp_path: Path):
    weknora = RecordingBackend(supports_folders=True)
    flat = RecordingBackend(supports_folders=False)
    service = _service(wm_paths, tmp_path, {"weknora": weknora, "flat": flat})
    kb = service.create_kb("root", "docs", "Docs", "")
    root = service.store.get_root_folder(kb["id"])
    folder_a = service.create_folder("root", "docs", "A", root["id"])
    folder_b = service.create_folder("root", "docs", "B", folder_a["id"])
    source = tmp_path / "guide.md"
    source.write_text("# Guide", encoding="utf-8")

    doc = service.add_document("root", source, ["docs"], later=True, folder_id=folder_b["id"])
    service.sync("root", all_users=False)

    assert weknora.uploads[-1]["remote_path"] == "A/B/guide.md"
    assert flat.uploads[-1]["remote_path"] is None
    assert flat.uploads[-1]["filename"] == "guide.md"
    assert service.store.get_backend_folder_mapping(kb["id"], "weknora", folder_b["id"])["backend_folder_id"] == "A/B"
    assert service.store.get_backend_folder_mapping(kb["id"], "flat", folder_b["id"]) is None

    service.place_document("root", doc["slug"], "docs", root["id"])
    jobs = service.status("root")["jobs"]
    assert jobs[-1]["operation"] == "move"
    assert not any(job["operation"] == "move" and job["backend_slug"] == "flat" for job in jobs)

    service.sync("root", all_users=False)
    assert weknora.moves[-1]["remote_path"] == "guide.md"
    assert service.store.get_backend_folder_mapping(kb["id"], "weknora", root["id"])["backend_folder_id"] == ""


def test_folder_capable_sync_preserves_nested_archive_filename_but_flat_uses_basename(
    wm_paths, tmp_path: Path
):
    weknora = RecordingBackend(supports_folders=True)
    flat = RecordingBackend(supports_folders=False)
    service = _service(wm_paths, tmp_path, {"weknora": weknora, "flat": flat})
    kb = service.create_kb("root", "docs", "Docs", "")
    root = service.store.get_root_folder(kb["id"])
    selected = service.create_folder("root", "docs", "Selected", root["id"])
    archive = tmp_path / "nested.zip"
    with zipfile.ZipFile(archive, "w") as outer:
        outer.writestr("manuals/api/guide.md", b"nested guide")

    service.add_document("root", archive, ["docs"], later=True, folder_id=selected["id"])
    service.sync("root", all_users=False)

    assert weknora.uploads[-1]["filename"] == "guide.md"
    assert weknora.uploads[-1]["remote_path"] == "Selected/manuals/api/guide.md"
    assert flat.uploads[-1]["filename"] == "guide.md"
    assert flat.uploads[-1]["remote_path"] is None


def test_folder_move_skips_flat_reupload_but_keeps_content_update_and_moves_weknora(
    wm_paths, tmp_path: Path
):
    weknora = RecordingBackend(supports_folders=True)
    flat = RecordingBackend(supports_folders=False)
    service = _service(wm_paths, tmp_path, {"weknora": weknora, "flat": flat})
    kb = service.create_kb("root", "docs", "Docs", "")
    root = service.store.get_root_folder(kb["id"])
    folder = service.create_folder("root", "docs", "A", root["id"])
    source = tmp_path / "guide.md"
    source.write_text("# Guide", encoding="utf-8")

    doc = service.add_document("root", source, ["docs"], later=True, folder_id=folder["id"])
    service.sync("root", all_users=False)

    updated_source = tmp_path / "guide-v2.md"
    updated_source.write_text("# Guide v2", encoding="utf-8")
    service.update_document("root", doc["slug"], updated_source, later=True)
    before_move = service.status("root")["jobs"]
    before_ids = {job["id"] for job in before_move}

    service.place_document("root", doc["slug"], "docs", root["id"])

    new_jobs = [job for job in service.status("root")["jobs"] if job["id"] not in before_ids]
    assert [(job["backend_slug"], job["operation"]) for job in new_jobs] == [("weknora", "move")]
    flat_update = next(
        job
        for job in before_move
        if job["backend_slug"] == "flat" and job["operation"] == "update"
    )
    assert next(job for job in service.status("root")["jobs"] if job["id"] == flat_update["id"])["status"] == "pending"


def test_git_sync_moves_unchanged_document_when_repository_folder_changes(
    wm_paths, tmp_path: Path
):
    weknora = RecordingBackend(supports_folders=True)
    flat = RecordingBackend(supports_folders=False)
    service = _service(wm_paths, tmp_path, {"weknora": weknora, "flat": flat})
    kb = service.create_kb("root", "docs", "Docs", "")
    repo_path = tmp_path / "repo"
    old_path = repo_path / "A" / "guide.md"
    old_path.parent.mkdir(parents=True)
    old_path.write_text("# Guide", encoding="utf-8")
    service.store.upsert_code_repository(
        repo_key="docs-repo", name="Docs Repo", git_url="", branch="main", auth_ref="",
        description="", tags=[], category_key="", sync_interval_minutes=60,
        auto_understand=False, status="active",
    )
    service.store.mark_code_repository_sync(
        "docs-repo", local_path=str(repo_path), last_commit="test", success=True, error=None
    )
    service.store.upsert_kb_repo_source(kb["id"], "docs-repo", [".md"])
    service.codegraph.sync_repository = lambda actor, repo_key: None

    first_sync = service.sync_kb_repo_source("root", "docs", "docs-repo")
    assert first_sync["added"] == 1
    doc = service.store.get_document_by_slug("guide")
    assert doc is not None
    service.sync("root", all_users=False)
    before_job_ids = {job["id"] for job in service.status("root")["jobs"]}

    new_path = repo_path / "B" / "guide.md"
    new_path.parent.mkdir()
    old_path.rename(new_path)
    result = service.sync_kb_repo_source("root", "docs", "docs-repo")

    assert result == {
        "kb_slug": "docs", "repo_key": "docs-repo",
        "added": 0, "removed": 0, "updated": 0, "unchanged": 1,
    }
    assert service.store.get_document_by_slug("guide")["id"] == doc["id"]
    placement = service.store.get_document_placement(doc["id"], kb["id"])
    assert placement["folder_path"] == "B"
    new_jobs = [
        job for job in service.status("root")["jobs"] if job["id"] not in before_job_ids
    ]
    assert [(job["backend_slug"], job["operation"]) for job in new_jobs] == [
        ("weknora", "move")
    ]
    assert not any(job["backend_slug"] == "flat" for job in new_jobs)

    service.sync("root", all_users=False)
    assert weknora.moves[-1]["remote_path"] == "B/guide.md"
    assert flat.moves == []


def test_place_document_same_folder_is_a_noop(wm_paths, tmp_path: Path, monkeypatch):
    weknora = RecordingBackend(supports_folders=True)
    service = _service(wm_paths, tmp_path, {"weknora": weknora})
    kb = service.create_kb("root", "docs", "Docs", "")
    root = service.store.get_root_folder(kb["id"])
    folder = service.create_folder("root", "docs", "A", root["id"])
    source = tmp_path / "guide.md"
    source.write_text("# Guide", encoding="utf-8")
    doc = service.add_document("root", source, ["docs"], later=True, folder_id=folder["id"])
    service.sync("root", all_users=False)
    placement_before = service.store.get_document_placement(doc["id"], kb["id"])
    jobs_before = service.status("root")["jobs"]

    def unexpected_placement_update(*args, **kwargs):
        raise AssertionError("same-folder placement must not update document_kbs")

    monkeypatch.setattr(service.store, "update_document_placement", unexpected_placement_update)
    result = service.place_document("root", doc["slug"], "docs", folder["id"])

    assert result == {**placement_before, "slug": doc["slug"], "kb": "docs"}
    assert service.store.get_document_placement(doc["id"], kb["id"]) == placement_before
    assert service.status("root")["jobs"] == jobs_before


def test_successive_folder_moves_only_sync_latest_placement(wm_paths, tmp_path: Path):
    weknora = RecordingBackend(supports_folders=True)
    service = _service(wm_paths, tmp_path, {"weknora": weknora})
    kb = service.create_kb("root", "docs", "Docs", "")
    root = service.store.get_root_folder(kb["id"])
    first = service.create_folder("root", "docs", "First", root["id"])
    latest = service.create_folder("root", "docs", "Latest", root["id"])
    source = tmp_path / "guide.md"
    source.write_text("# Guide", encoding="utf-8")

    doc = service.add_document("root", source, ["docs"], later=True, folder_id=first["id"])
    service.sync("root", all_users=False)

    service.place_document("root", doc["slug"], "docs", root["id"])
    service.place_document("root", doc["slug"], "docs", latest["id"])

    placement = service.store.get_document_placement(doc["id"], kb["id"])
    assert placement["folder_id"] == latest["id"]
    move_jobs = [job for job in service.status("root")["jobs"] if job["operation"] == "move"]
    assert [job["status"] for job in move_jobs] == ["cancelled", "pending"]

    result = service.sync("root", all_users=False)
    assert result == {"processed": 1, "succeeded": 1, "failed": 0}
    assert len(weknora.moves) == 1
    assert weknora.moves[-1]["remote_path"] == "Latest/guide.md"


def test_folder_rename_queues_moves_for_descendant_documents(wm_paths, tmp_path: Path):
    weknora = RecordingBackend(supports_folders=True)
    service = _service(wm_paths, tmp_path, {"weknora": weknora})
    kb = service.create_kb("root", "docs", "Docs", "")
    root = service.store.get_root_folder(kb["id"])
    folder_a = service.create_folder("root", "docs", "A", root["id"])
    folder_b = service.create_folder("root", "docs", "B", folder_a["id"])
    source = tmp_path / "guide.md"
    source.write_text("# Guide", encoding="utf-8")
    doc = service.add_document("root", source, ["docs"], later=True, folder_id=folder_b["id"])
    service.sync("root", all_users=False)

    service.update_folder("root", "docs", folder_a["id"], name="Renamed")
    pending = [job for job in service.status("root")["jobs"] if job["status"] == "pending"]
    assert [(job["doc_id"], job["operation"]) for job in pending] == [(doc["id"], "move")]
    service.sync("root", all_users=False)
    assert weknora.moves[-1]["remote_path"] == "Renamed/B/guide.md"


def test_failed_move_records_retryable_job_and_sync_error(wm_paths, tmp_path: Path):
    weknora = RecordingBackend(supports_folders=True)
    service = _service(wm_paths, tmp_path, {"weknora": weknora})
    kb = service.create_kb("root", "docs", "Docs", "")
    root = service.store.get_root_folder(kb["id"])
    folder = service.create_folder("root", "docs", "A", root["id"])
    source = tmp_path / "guide.md"
    source.write_text("# Guide", encoding="utf-8")
    doc = service.add_document("root", source, ["docs"], later=True, folder_id=folder["id"])
    service.sync("root", all_users=False)
    service.place_document("root", doc["slug"], "docs", root["id"])
    move_job_id = next(
        job["id"]
        for job in service.status("root")["jobs"]
        if job["operation"] == "move" and job["status"] == "pending"
    )

    def fail_move(*args, **kwargs):
        raise RuntimeError("Weknora API error: knowledge base not found (404, code 1003)")

    weknora.move = fail_move  # type: ignore[method-assign]
    result = service.sync("root", all_users=False)

    jobs = service.status("root")["jobs"]
    assert any(job["id"] == move_job_id for job in jobs), "move job must not be removed by KB rebuild"
    job = next(job for job in jobs if job["id"] == move_job_id)
    state = service.store.get_sync_state(doc["id"], kb["id"], "weknora")
    assert job["operation"] == "move"
    assert job["status"] == "failed"
    assert "knowledge base not found" in job["error"]
    assert state["status"] == "sync_failed"
    assert "knowledge base not found" in state["backend_error"]
    assert weknora.created_kbs == [("docs", "Docs")]
    assert result == {"processed": 1, "succeeded": 0, "failed": 1}
