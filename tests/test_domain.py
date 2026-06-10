from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from agent_bridge.core.domain import (
    AccessDenied,
    BackendAdapter,
    BackendDocStatus,
    DocumentStatus,
    KbRole,
    Operation,
    RetrievalStrategy,
    SyncJobStatus,
    SyncStateStatus,
    can_manage_kb,
    can_view_kb,
    can_write_own_doc,
    require_admin_user,
)
from agent_bridge.knowledge.backends.mock import MockBackend
from agent_bridge.core.slug import make_slug, unique_slug


def test_domain_enums_have_expected_values() -> None:
    assert KbRole.viewer.value == "viewer"
    assert KbRole.contributor.value == "contributor"
    assert KbRole.admin.value == "admin"
    assert DocumentStatus.active.value == "active"
    assert DocumentStatus.deleted.value == "deleted"
    assert SyncJobStatus.pending.value == "pending"
    assert SyncStateStatus.delete_failed.value == "delete_failed"


def test_slug_generation_keeps_readable_ascii_and_chinese() -> None:
    assert make_slug("API 说明 v2.pdf") == "api-说明-v2"
    assert make_slug("  Front End Guide.docx ") == "front-end-guide"


def test_slug_generation_falls_back_to_document() -> None:
    assert make_slug("!!!.pdf") == "document"


def test_unique_slug_adds_numeric_suffix() -> None:
    assert unique_slug("guide", {"guide", "guide-2"}) == "guide-3"


def test_permissions_by_role() -> None:
    assert can_view_kb(KbRole.viewer)
    assert can_view_kb(KbRole.contributor)
    assert can_view_kb(KbRole.admin)
    assert not can_write_own_doc(KbRole.viewer)
    assert can_write_own_doc(KbRole.contributor)
    assert can_manage_kb(KbRole.admin)


def test_global_admin_required() -> None:
    require_admin_user("root", {"root"})
    with pytest.raises(AccessDenied):
        require_admin_user("alice", {"root"})


def test_operation_values_are_stable() -> None:
    assert Operation.create.value == "create"
    assert Operation.update.value == "update"
    assert Operation.delete.value == "delete"


def test_backend_adapter_protocol_is_defined():
    from agent_bridge.core.domain import BackendAdapter

    assert BackendAdapter is not None


def test_backend_doc_status_defaults():
    status = BackendDocStatus(status="completed", chunk_count=5, progress=1.0, error_message=None)
    assert status.status == "completed"
    assert status.chunk_count == 5


def test_mock_backend_create_kb_returns_slug():
    with tempfile.TemporaryDirectory() as tmp:
        backend = MockBackend(Path(tmp))
        kb_id = backend.create_kb("test-kb", "Test KB")
        assert kb_id == "test-kb"


def test_mock_backend_upload_returns_doc_id():
    with tempfile.TemporaryDirectory() as tmp:
        backend = MockBackend(Path(tmp))
        file_path = Path(tmp) / "test.pdf"
        file_path.write_bytes(b"content")
        doc_id = backend.upload("test-kb", "test-doc", file_path, "test.pdf")
        assert doc_id == "test-kb:test-doc"


def test_mock_backend_get_status_returns_completed():
    with tempfile.TemporaryDirectory() as tmp:
        backend = MockBackend(Path(tmp))
        file_path = Path(tmp) / "test.pdf"
        file_path.write_bytes(b"content")
        backend.upload("test-kb", "test-doc", file_path, "test.pdf")
        status = backend.get_status("test-kb", "test-kb:test-doc")
        assert status.status == "completed"
        assert status.chunk_count == 1


def test_mock_backend_delete_removes_document():
    with tempfile.TemporaryDirectory() as tmp:
        backend = MockBackend(Path(tmp))
        file_path = Path(tmp) / "test.pdf"
        file_path.write_bytes(b"content")
        doc_id = backend.upload("test-kb", "test-doc", file_path, "test.pdf")
        backend.delete("test-kb", doc_id)
        assert backend.get_status("test-kb", doc_id).status == "not_found"


def test_retrieval_result_dataclass():
    from agent_bridge.core.domain import RetrievalResult

    r = RetrievalResult(
        chunk_id="c1",
        content="some text",
        document_name="guide.md",
        similarity=0.92,
        dataset_id="ds1",
    )
    assert r.chunk_id == "c1"
    assert r.similarity == 0.92


def test_ask_result_dataclass():
    from agent_bridge.core.domain import AskResult

    result = AskResult(answer="yes", chunks=[], session_id="s1")
    assert result.answer == "yes"
    assert result.session_id == "s1"
    assert result.chunks == []


def test_backend_adapter_protocol_has_retrieve_and_ask():
    from agent_bridge.core.domain import BackendAdapter
    import inspect

    sig = inspect.signature(BackendAdapter.retrieve)
    assert "question" in sig.parameters
    assert "backend_kb_id" in sig.parameters

    sig = inspect.signature(BackendAdapter.ask)
    assert "question" in sig.parameters
    assert "backend_kb_id" in sig.parameters


def test_mock_backend_retrieve_returns_empty():
    with tempfile.TemporaryDirectory() as tmp:
        backend = MockBackend(Path(tmp))
        results = backend.retrieve("test-kb", "what is X?")
        assert results == []


def test_mock_backend_ask_returns_fallback():
    with tempfile.TemporaryDirectory() as tmp:
        backend = MockBackend(Path(tmp))
        result, chat_id = backend.ask("test-kb", "what is X?")
        assert "does not support" in result.answer
        assert result.chunks == []
        assert chat_id == ""


def test_retrieval_strategy_dataclass():
    s = RetrievalStrategy(backend_slug="weknora", agent_id="hybrid-rag-wiki")
    assert s.backend_slug == "weknora"
    assert s.agent_id == "hybrid-rag-wiki"


def test_retrieval_strategy_agent_id_optional():
    s = RetrievalStrategy(backend_slug="ragflow")
    assert s.agent_id is None


def test_backend_adapter_ask_accepts_agent_id():
    """Verify the protocol's ask() signature includes agent_id."""
    import inspect
    sig = inspect.signature(BackendAdapter.ask)
    params = list(sig.parameters.keys())
    assert "agent_id" in params
