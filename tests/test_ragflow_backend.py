from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from agent_bridge.domain import AskResult, BackendDocStatus, RetrievalResult
from agent_bridge.ragflow_backend import RagFlowBackend


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


def test_delete_kb_uses_bulk_dataset_delete(respx_mock):
    base_url = "http://localhost:9380"
    route = respx_mock.delete(f"{base_url}/api/v1/datasets").mock(
        return_value=httpx.Response(200, json={"code": 0, "data": True})
    )
    backend = RagFlowBackend(base_url=base_url, api_key="test-key", timeout=30)
    backend.delete_kb("ds-123")
    assert route.calls.last.request.read() == b'{"ids":["ds-123"]}'


def test_ragflow_business_error_raises(respx_mock):
    base_url = "http://localhost:9380"
    respx_mock.post(f"{base_url}/api/v1/retrieval").mock(
        return_value=httpx.Response(
            200,
            json={"code": 100, "data": None, "message": "Model Name is required"},
        )
    )
    backend = RagFlowBackend(base_url=base_url, api_key="test-key", timeout=30)
    with pytest.raises(RuntimeError, match="Model Name is required"):
        backend.retrieve("ds-123", "test")


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


def test_ragflow_retrieve_returns_chunks(respx_mock):
    backend = RagFlowBackend(base_url="http://localhost:9380", api_key="test-key")

    respx_mock.post("http://localhost:9380/api/v1/retrieval").mock(
        return_value=httpx.Response(200, json={
            "code": 0,
            "data": {"chunks": [
                {
                    "id": "chunk-1",
                    "content": "RagFlow is a RAG engine.",
                    "document_keyword": "intro.md",
                    "similarity": 0.95,
                    "dataset_id": "ds-abc",
                },
                {
                    "id": "chunk-2",
                    "content": "It supports knowledge bases.",
                    "document_keyword": "guide.md",
                    "similarity": 0.80,
                    "dataset_id": "ds-abc",
                },
            ]},
        })
    )

    results = backend.retrieve("ds-abc", "what is RagFlow?")
    assert len(results) == 2
    assert results[0].chunk_id == "chunk-1"
    assert results[0].similarity == 0.95
    assert results[0].document_name == "intro.md"
    assert results[1].content == "It supports knowledge bases."


def test_ragflow_retrieve_empty_results(respx_mock):
    backend = RagFlowBackend(base_url="http://localhost:9380", api_key="test-key")

    respx_mock.post("http://localhost:9380/api/v1/retrieval").mock(
        return_value=httpx.Response(200, json={"code": 0, "data": {"chunks": []}})
    )

    results = backend.retrieve("ds-abc", "nonexistent topic")
    assert results == []


def test_ragflow_ask_creates_chat_and_session(respx_mock):
    backend = RagFlowBackend(base_url="http://localhost:9380", api_key="test-key")

    # Mock chat assistant creation
    respx_mock.post("http://localhost:9380/api/v1/chats").mock(
        return_value=httpx.Response(200, json={"code": 0, "data": {"id": "chat-123"}})
    )
    # Mock session creation
    respx_mock.post("http://localhost:9380/api/v1/chats/chat-123/sessions").mock(
        return_value=httpx.Response(200, json={"code": 0, "data": {"id": "sess-456"}})
    )
    # Mock chat completions
    respx_mock.post("http://localhost:9380/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={
            "code": 0,
            "data": {"answer": "RagFlow is a RAG engine.", "reference": {}},
        })
    )

    result, chat_id = backend.ask("ds-abc", "what is RagFlow?")
    assert isinstance(result, AskResult)
    assert result.answer == "RagFlow is a RAG engine."
    assert result.session_id == "sess-456"
    assert chat_id == "chat-123"


def test_ragflow_ask_reuses_chat_id(respx_mock):
    backend = RagFlowBackend(base_url="http://localhost:9380", api_key="test-key")

    # Only mock session creation and completions (no chat creation)
    respx_mock.post("http://localhost:9380/api/v1/chats/chat-existing/sessions").mock(
        return_value=httpx.Response(200, json={"code": 0, "data": {"id": "sess-789"}})
    )
    respx_mock.post("http://localhost:9380/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={
            "code": 0,
            "data": {"answer": "It works.", "reference": {}},
        })
    )

    result, chat_id = backend.ask("ds-abc", "test?", chat_id="chat-existing")
    assert chat_id == "chat-existing"
    assert result.answer == "It works."


def test_ragflow_ask_creates_unique_chat_names(respx_mock):
    backend = RagFlowBackend(base_url="http://localhost:9380", api_key="test-key")
    chat_requests = []

    def create_chat(request):
        chat_requests.append(json.loads(request.content))
        return httpx.Response(200, json={"code": 0, "data": {"id": f"chat-{len(chat_requests)}"}})

    respx_mock.post("http://localhost:9380/api/v1/chats").mock(side_effect=create_chat)
    respx_mock.post(url__regex=r"http://localhost:9380/api/v1/chats/chat-.*/sessions").mock(
        return_value=httpx.Response(200, json={"code": 0, "data": {"id": "sess-1"}})
    )
    respx_mock.post("http://localhost:9380/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={
            "code": 0,
            "data": {"answer": "It works.", "reference": {}},
        })
    )

    backend.ask("ds-abc", "first?")
    backend.ask("ds-abc", "second?")

    assert chat_requests[0]["name"] != chat_requests[1]["name"]
