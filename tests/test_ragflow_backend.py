from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from wiki_manager.domain import BackendDocStatus
from wiki_manager.ragflow_backend import RagFlowBackend


def test_create_kb(respx_mock):
    base_url = "http://localhost:9380"
    respx_mock.post(f"{base_url}/api/v1/datasets").mock(
        return_value=httpx.Response(200, json={"data": {"id": "ds-123"}})
    )
    backend = RagFlowBackend(base_url=base_url, api_key="test-key", timeout=30)
    kb_id = backend.create_kb("test-kb", "Test KB")
    assert kb_id == "ds-123"


def test_upload(respx_mock, tmp_path):
    base_url = "http://localhost:9380"
    respx_mock.post(f"{base_url}/api/v1/datasets/ds-123/documents").mock(
        return_value=httpx.Response(200, json={"data": {"id": "doc-456"}})
    )
    backend = RagFlowBackend(base_url=base_url, api_key="test-key", timeout=30)
    file_path = tmp_path / "test.pdf"
    file_path.write_bytes(b"content")
    doc_id = backend.upload("ds-123", "test-doc", file_path, "test.pdf")
    assert doc_id == "doc-456"


def test_delete(respx_mock):
    base_url = "http://localhost:9380"
    respx_mock.delete(f"{base_url}/api/v1/datasets/ds-123/documents").mock(
        return_value=httpx.Response(200, json={"code": 0, "data": {"deleted": 1}})
    )
    backend = RagFlowBackend(base_url=base_url, api_key="test-key", timeout=30)
    backend.delete("ds-123", "doc-456")


def test_get_status_completed(respx_mock):
    base_url = "http://localhost:9380"
    respx_mock.get(f"{base_url}/api/v1/datasets/ds-123/documents").mock(
        return_value=httpx.Response(200, json={"data": {"docs": [{"run": "DONE", "chunk_count": 10, "progress": 1.0}]}})
    )
    backend = RagFlowBackend(base_url=base_url, api_key="test-key", timeout=30)
    status = backend.get_status("ds-123", "doc-456")
    assert status.status == "completed"
    assert status.chunk_count == 10
    assert status.progress == 1.0


def test_get_status_parsing(respx_mock):
    base_url = "http://localhost:9380"
    respx_mock.get(f"{base_url}/api/v1/datasets/ds-123/documents").mock(
        return_value=httpx.Response(200, json={"data": {"docs": [{"run": "RUNNING", "chunk_count": 0, "progress": 0.5}]}})
    )
    backend = RagFlowBackend(base_url=base_url, api_key="test-key", timeout=30)
    status = backend.get_status("ds-123", "doc-456")
    assert status.status == "parsing"
    assert status.progress == 0.5


def test_create_kb_failure(respx_mock):
    base_url = "http://localhost:9380"
    respx_mock.post(f"{base_url}/api/v1/datasets").mock(
        return_value=httpx.Response(401, json={"code": 401, "message": "Unauthorized"})
    )
    backend = RagFlowBackend(base_url=base_url, api_key="bad-key", timeout=30)
    with pytest.raises(RuntimeError, match="401"):
        backend.create_kb("test-kb", "Test KB")
