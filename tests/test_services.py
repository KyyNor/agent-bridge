from __future__ import annotations

from pathlib import Path

import pytest

from agent_bridge.core.config import BackendConfig, AgentBridgePaths, ensure_directories
from agent_bridge.core.domain import AccessDenied, KbRole, NotFound, SyncStateStatus, ValidationError
from agent_bridge.knowledge.backends.registry import BackendRegistry
from agent_bridge.knowledge.service import AgentBridgeService


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


def test_admin_creates_kb_and_grants_member(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    kb = service.create_kb(actor="root", slug="frontend-docs", name="Frontend Docs", description="")
    service.grant_kb_member(actor="root", kb_slug="frontend-docs", linux_user="alice", role=KbRole.contributor)
    assert kb["slug"] == "frontend-docs"
    assert service.list_kbs(actor="alice")[0]["slug"] == "frontend-docs"


def test_non_admin_cannot_create_kb(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    with pytest.raises(AccessDenied):
        service.create_kb(actor="alice", slug="frontend-docs", name="Frontend Docs", description="")


def test_contributor_adds_doc_to_multiple_kbs(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    service.create_kb("root", "backend-docs", "Backend Docs", "")
    service.grant_kb_member("root", "frontend-docs", "alice", KbRole.contributor)
    service.grant_kb_member("root", "backend-docs", "alice", KbRole.contributor)
    source = tmp_path / "接口说明.pdf"
    source.write_bytes(b"version one")
    doc = service.add_document(actor="alice", source=source, kb_slugs=["frontend-docs", "backend-docs"], later=True)
    assert doc["slug"] == "接口说明"
    assert doc["current_version_no"] == 1
    assert len(service.list_docs(actor="alice", kb_slug="frontend-docs")) == 1
    assert len(service.list_docs(actor="alice", kb_slug="backend-docs")) == 1


def test_update_document_creates_new_version(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    service.grant_kb_member("root", "frontend-docs", "alice", KbRole.contributor)
    v1 = tmp_path / "Guide.pdf"
    v2 = tmp_path / "Guide-v2.pdf"
    v1.write_bytes(b"one")
    v2.write_bytes(b"two")
    doc = service.add_document("alice", v1, ["frontend-docs"], later=True)
    updated = service.update_document("alice", doc["slug"], v2, later=True)
    assert updated["current_version_no"] == 2


def test_viewer_cannot_add_document(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    service.grant_kb_member("root", "frontend-docs", "bob", KbRole.viewer)
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"one")
    with pytest.raises(AccessDenied):
        service.add_document("bob", source, ["frontend-docs"], later=True)


def test_invisible_document_edits_return_not_found(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    service.grant_kb_member("root", "frontend-docs", "alice", KbRole.contributor)
    v1 = tmp_path / "Guide.pdf"
    v2 = tmp_path / "Guide-v2.pdf"
    v1.write_bytes(b"one")
    v2.write_bytes(b"two")
    doc = service.add_document("alice", v1, ["frontend-docs"], later=True)

    with pytest.raises(NotFound, match="document not found"):
        service.update_document("bob", doc["slug"], v2, later=True)
    with pytest.raises(NotFound, match="document not found"):
        service.delete_document("bob", doc["slug"])
    with pytest.raises(NotFound, match="document not found"):
        service.purge_document("bob", doc["slug"], confirm=True)


def test_shared_document_requires_admin_for_all_associated_kbs(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "kb-a", "KB A", "")
    service.create_kb("root", "kb-b", "KB B", "")
    service.grant_kb_member("root", "kb-a", "alice", KbRole.contributor)
    service.grant_kb_member("root", "kb-b", "alice", KbRole.contributor)
    service.grant_kb_member("root", "kb-a", "carol", KbRole.admin)
    service.grant_kb_member("root", "kb-b", "carol", KbRole.viewer)
    v1 = tmp_path / "Guide.pdf"
    v2 = tmp_path / "Guide-v2.pdf"
    v1.write_bytes(b"one")
    v2.write_bytes(b"two")
    doc = service.add_document("alice", v1, ["kb-a", "kb-b"], later=True)

    with pytest.raises(AccessDenied):
        service.update_document("carol", doc["slug"], v2, later=True)
    with pytest.raises(AccessDenied):
        service.delete_document("carol", doc["slug"])
    with pytest.raises(AccessDenied):
        service.purge_document("carol", doc["slug"], confirm=True)

    service.grant_kb_member("root", "kb-b", "carol", KbRole.admin)
    updated = service.update_document("carol", doc["slug"], v2, later=True)
    assert updated["current_version_no"] == 2


def test_purge_requires_confirmation(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    service.grant_kb_member("root", "frontend-docs", "alice", KbRole.contributor)
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"one")
    doc = service.add_document("alice", source, ["frontend-docs"], later=True)

    with pytest.raises(ValidationError, match="purge requires confirmation"):
        service.purge_document("alice", doc["slug"])


def test_purge_keeps_shared_archive_until_last_reference_is_removed(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    service.grant_kb_member("root", "frontend-docs", "alice", KbRole.contributor)
    first = tmp_path / "Guide-A.pdf"
    second = tmp_path / "Guide-B.pdf"
    first.write_bytes(b"same bytes")
    second.write_bytes(b"same bytes")
    doc_a = service.add_document("alice", first, ["frontend-docs"], later=True)
    doc_b = service.add_document("alice", second, ["frontend-docs"], later=True)
    version_b = service.store.list_versions(doc_b["id"])[0]
    archive_path = Path(version_b["archive_path"])

    assert archive_path.exists()
    service.purge_document("alice", doc_a["slug"], confirm=True)

    assert archive_path.exists()
    assert archive_path.read_bytes() == b"same bytes"
    assert service.store.list_versions(doc_b["id"])[0]["archive_path"] == str(archive_path)

    service.purge_document("alice", doc_b["slug"], confirm=True)
    assert not archive_path.exists()


def test_get_doc_does_not_expose_archive_paths_to_viewer(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    service.grant_kb_member("root", "frontend-docs", "alice", KbRole.contributor)
    service.grant_kb_member("root", "frontend-docs", "bob", KbRole.viewer)
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"one")
    doc = service.add_document("alice", source, ["frontend-docs"], later=True)

    visible = service.get_doc("bob", doc["slug"])

    assert visible["versions"]
    assert "archive_path" not in visible["versions"][0]


def test_invisible_kb_returns_not_found(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    with pytest.raises(NotFound):
        service.list_docs(actor="alice", kb_slug="frontend-docs")


def test_immediate_add_syncs_to_mock_backend(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    service.grant_kb_member("root", "frontend-docs", "alice", KbRole.contributor)
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"one")
    service.add_document("alice", source, ["frontend-docs"], later=False)
    status = service.status(actor="alice")
    assert status["jobs"][0]["status"] == "succeeded"
    docs = service.list_docs(actor="alice", kb_slug="frontend-docs")
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

        def upload(self, backend_kb_id: str, doc_slug: str, file_path: Path, filename: str) -> str:
            self.upload_kb_ids.append(backend_kb_id)
            return "backend-doc-456"

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
    service.grant_kb_member("root", "frontend-docs", "alice", KbRole.contributor)
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"one")

    doc = service.add_document("alice", source, ["frontend-docs"], later=False)
    service.delete_document("alice", doc["slug"], later=False)

    assert backend.upload_kb_ids == ["backend-dataset-123"]
    assert backend.delete_kb_ids == ["backend-dataset-123"]


def test_sync_processes_later_job(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    service.grant_kb_member("root", "frontend-docs", "alice", KbRole.contributor)
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"one")
    service.add_document("alice", source, ["frontend-docs"], later=True)
    before = service.status(actor="alice")
    assert before["jobs"][0]["status"] == "pending"
    result = service.sync(actor="alice", all_users=False)
    assert result["processed"] == 1
    after = service.status(actor="alice")
    assert after["jobs"][0]["status"] == "succeeded"


def test_delete_creates_delete_job_and_sync_marks_deleted(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    kb = service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    service.grant_kb_member("root", "frontend-docs", "alice", KbRole.contributor)
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"one")
    doc = service.add_document("alice", source, ["frontend-docs"], later=False)
    service.delete_document("alice", doc["slug"])

    before = service.status(actor="alice")
    assert before["jobs"][-1]["operation"] == "delete"
    assert before["jobs"][-1]["status"] == "pending"

    service.sync(actor="alice", all_users=False)

    after = service.status(actor="alice")
    assert after["jobs"][-1]["status"] == "succeeded"
    sync_state = service.store.get_sync_state(doc["id"], kb["id"], backend_slug="mock")
    assert sync_state is not None
    assert sync_state["status"] == "deleted"


def test_delete_with_later_false_syncs_immediately(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    service.grant_kb_member("root", "frontend-docs", "alice", KbRole.contributor)
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"one")
    doc = service.add_document("alice", source, ["frontend-docs"], later=False)

    service.delete_document("alice", doc["slug"], later=False)

    status = service.status(actor="alice")
    assert status["jobs"][-1]["operation"] == "delete"
    assert status["jobs"][-1]["status"] == "succeeded"


def test_sync_failure_updates_sync_state(wm_paths, tmp_path: Path, monkeypatch) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    kb = service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    service.grant_kb_member("root", "frontend-docs", "alice", KbRole.contributor)
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"one")
    doc = service.add_document("alice", source, ["frontend-docs"], later=True)

    def fail_upsert(*args, **kwargs):
        raise RuntimeError("backend down")

    # Monkeypatch the registry adapter, not mock_backend directly
    mock_adapter = service.registry.get("mock")
    monkeypatch.setattr(mock_adapter, "upload", fail_upsert)
    service.sync("alice", all_users=False)

    status = service.status("alice")
    assert status["jobs"][0]["status"] == "failed"
    sync_state = service.store.get_sync_state(doc["id"], kb["id"])
    assert sync_state is not None
    assert sync_state["status"] == SyncStateStatus.sync_failed.value


def test_delete_failure_updates_sync_state(wm_paths, tmp_path: Path, monkeypatch) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    kb = service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    service.grant_kb_member("root", "frontend-docs", "alice", KbRole.contributor)
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"one")
    doc = service.add_document("alice", source, ["frontend-docs"], later=False)
    service.delete_document("alice", doc["slug"])

    def fail_delete(*args, **kwargs):
        raise RuntimeError("backend down")

    mock_adapter = service.registry.get("mock")
    monkeypatch.setattr(mock_adapter, "delete", fail_delete)
    service.sync("alice", all_users=False)

    status = service.status("alice")
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
    with pytest.raises(NotFound, match="no.*backend"):
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
