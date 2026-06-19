from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from agent_bridge.core.domain import AskResult
from agent_bridge.knowledge_management.docs_knowledge.backends.weknora import WeknoraBackend


def test_create_kb_posts_document_kb(respx_mock):
    base_url = "http://localhost"
    respx_mock.get(f"{base_url}/api/v1/models").mock(
        return_value=httpx.Response(200, json={"success": True, "data": []})
    )
    route = respx_mock.post(f"{base_url}/api/v1/knowledge-bases").mock(
        return_value=httpx.Response(201, json={"success": True, "data": {"id": "kb-123"}})
    )

    backend = WeknoraBackend(
        base_url=base_url,
        api_key="test-key",
        embedding_model_id="emb-1",
        summary_model_id="chat-1",
        timeout=30,
    )
    kb_id = backend.create_kb("frontend-docs", "Frontend Docs")

    request = route.calls.last.request
    body = json.loads(request.content)
    assert kb_id == "kb-123"
    assert request.headers["X-API-Key"] == "test-key"
    assert body["name"] == "Frontend Docs"
    assert body["description"] == "frontend-docs"
    assert body["type"] == "document"
    assert body["embedding_model_id"] == "emb-1"
    assert body["summary_model_id"] == "chat-1"
    assert "parser_engine_rules" in body["chunking_config"]
    assert body["chunking_config"]["parser_engine_rules"][1] == {"file_types": ["docx", "doc"], "engine": "builtin"}
    assert body["indexing_strategy"]["wiki_enabled"] is True
    assert body["question_generation_config"]["enabled"] is True


def test_upload_uses_file_endpoint(respx_mock, tmp_path: Path):
    base_url = "http://localhost"
    route = respx_mock.post(f"{base_url}/api/v1/knowledge-bases/kb-123/knowledge/file").mock(
        return_value=httpx.Response(201, json={"success": True, "data": {"id": "know-456"}})
    )
    source = tmp_path / "guide.md"
    source.write_text("# Guide\n\nWeknora content.", encoding="utf-8")

    backend = WeknoraBackend(base_url=base_url, api_key="test-key")
    doc_id = backend.upload("kb-123", "guide", source, "guide.md")

    request = route.calls.last.request
    assert doc_id == "know-456"
    assert request.headers["X-API-Key"] == "test-key"
    assert "multipart/form-data" in request.headers["content-type"]


def test_get_status_maps_completed_and_failed(respx_mock):
    base_url = "http://localhost"
    backend = WeknoraBackend(base_url=base_url, api_key="test-key")

    respx_mock.get(f"{base_url}/api/v1/knowledge/know-complete").mock(
        return_value=httpx.Response(200, json={
            "success": True,
            "data": {
                "id": "know-complete",
                "parse_status": "completed",
                "chunk_count": 7,
                "error_message": "",
            },
        })
    )
    respx_mock.get(f"{base_url}/api/v1/knowledge/know-failed").mock(
        return_value=httpx.Response(200, json={
            "success": True,
            "data": {
                "id": "know-failed",
                "parse_status": "failed",
                "error_message": "embedding failed",
            },
        })
    )

    complete = backend.get_status("kb-123", "know-complete")
    failed = backend.get_status("kb-123", "know-failed")

    assert complete.status == "completed"
    assert complete.chunk_count == 7
    assert complete.progress == 1.0
    assert failed.status == "failed"
    assert failed.error_message == "embedding failed"


def test_get_status_404_maps_not_found(respx_mock):
    base_url = "http://localhost"
    respx_mock.get(f"{base_url}/api/v1/knowledge/missing").mock(
        return_value=httpx.Response(404, json={"success": False, "error": "not found"})
    )

    backend = WeknoraBackend(base_url=base_url, api_key="test-key")
    status = backend.get_status("kb-123", "missing")

    assert status.status == "not_found"


def test_retrieve_maps_results_and_applies_top_k(respx_mock):
    base_url = "http://localhost"
    route = respx_mock.post(f"{base_url}/api/v1/knowledge-search").mock(
        return_value=httpx.Response(200, json={
            "success": True,
            "data": [
                {
                    "id": "chunk-1",
                    "content": "Weknora supports hybrid search.",
                    "knowledge_title": "intro.md",
                    "score": 0.91,
                    "knowledge_base_id": "kb-123",
                },
                {
                    "id": "chunk-2",
                    "content": "Second result.",
                    "knowledge_filename": "guide.md",
                    "score": 0.75,
                    "knowledge_base_id": "kb-123",
                },
            ],
        })
    )

    backend = WeknoraBackend(base_url=base_url, api_key="test-key")
    results = backend.retrieve("kb-123", "hybrid search", top_k=1)

    assert json.loads(route.calls.last.request.content) == {
        "query": "hybrid search",
        "knowledge_base_id": "kb-123",
    }
    assert len(results) == 1
    assert results[0].chunk_id == "chunk-1"
    assert results[0].document_name == "intro.md"
    assert results[0].similarity == 0.91
    assert results[0].dataset_id == "kb-123"


def test_ask_creates_session_and_parses_sse(respx_mock):
    base_url = "http://localhost"
    respx_mock.post(f"{base_url}/api/v1/sessions").mock(
        return_value=httpx.Response(201, json={"success": True, "data": {"id": "sess-123"}})
    )
    chat_route = respx_mock.post(f"{base_url}/api/v1/knowledge-chat/sess-123").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=(
                'event: message\n'
                'data: {"response_type":"references","content":"","done":false,'
                '"knowledge_references":[{"id":"chunk-1","content":"Weknora fact",'
                '"knowledge_id":"know-1","knowledge_title":"intro.md","score":0.88}]}\n\n'
                'event: message\n'
                'data: {"response_type":"answer","content":"Weknora","done":false}\n\n'
                'event: message\n'
                'data: {"response_type":"answer","content":" works.","done":false}\n\n'
                'event: message\n'
                'data: {"response_type":"answer","content":"","done":true}\n\n'
            ),
        )
    )

    backend = WeknoraBackend(base_url=base_url, api_key="test-key")
    result, chat_id = backend.ask("kb-123", "what is Weknora?")

    assert isinstance(result, AskResult)
    assert result.answer == "Weknora works."
    assert result.session_id == "sess-123"
    assert chat_id == ""
    assert result.chunks[0].chunk_id == "chunk-1"
    assert result.chunks[0].content == "Weknora fact"
    assert json.loads(chat_route.calls.last.request.content) == {
        "query": "what is Weknora?",
        "knowledge_base_ids": ["kb-123"],
        "disable_title": True,
        "channel": "api",
    }


def test_ask_reuses_session_id(respx_mock):
    base_url = "http://localhost"
    respx_mock.post(f"{base_url}/api/v1/knowledge-chat/sess-existing").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text='event: message\ndata: {"response_type":"answer","content":"ok","done":true}\n\n',
        )
    )

    backend = WeknoraBackend(base_url=base_url, api_key="test-key")
    result, chat_id = backend.ask("kb-123", "continue?", chat_id="ignored", session_id="sess-existing")

    assert result.answer == "ok"
    assert result.session_id == "sess-existing"
    assert chat_id == "ignored"


def test_business_error_raises(respx_mock):
    base_url = "http://localhost"
    respx_mock.post(f"{base_url}/api/v1/knowledge-bases").mock(
        return_value=httpx.Response(200, json={"success": False, "message": "invalid model"})
    )

    backend = WeknoraBackend(base_url=base_url, api_key="test-key")
    with pytest.raises(RuntimeError, match="invalid model"):
        backend.create_kb("kb", "KB")


def test_sse_error_raises(respx_mock):
    base_url = "http://localhost"
    respx_mock.post(f"{base_url}/api/v1/sessions").mock(
        return_value=httpx.Response(201, json={"success": True, "data": {"id": "sess-123"}})
    )
    respx_mock.post(f"{base_url}/api/v1/knowledge-chat/sess-123").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text='event: message\ndata: {"response_type":"error","content":"model failed","done":true}\n\n',
        )
    )

    backend = WeknoraBackend(base_url=base_url, api_key="test-key")
    with pytest.raises(RuntimeError, match="model failed"):
        backend.ask("kb-123", "question")
