"""Tests for WeknoraBackend agent management methods."""
from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from agent_bridge.knowledge_management.docs_knowledge.backends.weknora import WeknoraBackend


@pytest.fixture
def backend():
    return WeknoraBackend(base_url="http://localhost", api_key="test-key")


def _mock_response(status_code=200, json_data=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text or json.dumps(json_data or {})
    resp.json.return_value = json_data or {}
    return resp


class _MockStreamResponse:
    """Mocks an httpx streaming response (context manager + iter_text)."""

    def __init__(self, text: str, status_code: int = 200):
        self.status_code = status_code
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_text(self):
        # Emit the full SSE payload as one chunk — _do_chat splits on \n\n.
        if self.text:
            yield self.text


def test_list_agents(backend):
    agents_data = {
        "data": [
            {"id": "builtin-smart-reasoning", "name": "Smart Reasoning", "is_builtin": True,
             "config": {"agent_type": "smart-reasoning"}},
        ],
        "success": True,
    }
    with patch("httpx.request", return_value=_mock_response(json_data=agents_data)):
        agents = backend.list_agents()
    assert len(agents) == 1
    assert agents[0]["id"] == "builtin-smart-reasoning"


def test_get_type_presets(backend):
    presets_data = {
        "data": [
            {"id": "hybrid-rag-wiki", "config": {"system_prompt_id": "hybrid_rag_wiki_agent"}},
        ],
        "success": True,
    }
    with patch("httpx.request", return_value=_mock_response(json_data=presets_data)):
        presets = backend.get_type_presets()
    assert len(presets) == 1
    assert presets[0]["id"] == "hybrid-rag-wiki"


def test_ensure_hybrid_agent_found_existing(backend):
    agents_data = {
        "data": [
            {"id": "builtin-smart-reasoning", "is_builtin": True, "config": {"agent_type": "smart-reasoning"}},
            {"id": "existing-hybrid-id", "is_builtin": False, "config": {"agent_type": "hybrid-rag-wiki"}},
        ],
        "success": True,
    }
    with patch("httpx.request", return_value=_mock_response(json_data=agents_data)):
        agent_id = backend.ensure_hybrid_agent()
    assert agent_id == "existing-hybrid-id"


def test_ensure_hybrid_agent_creates_new(backend):
    empty_agents = {"data": [
        {"id": "builtin-smart-reasoning", "is_builtin": True, "config": {"agent_type": "smart-reasoning"}},
    ], "success": True}
    presets = {"data": [
        {"id": "hybrid-rag-wiki", "config": {"system_prompt_id": "hybrid_rag_wiki_agent"}},
    ], "success": True}
    created = {"data": {"id": "new-hybrid-uuid", "name": "AgentBridge混合智能体"}, "success": True}

    def mock_request(method, url, **kwargs):
        if "/api/v1/agents/type-presets" in url:
            return _mock_response(json_data=presets)
        if "/api/v1/agents" in url and method == "GET":
            return _mock_response(json_data=empty_agents)
        if "/api/v1/agents" in url and method == "POST":
            return _mock_response(json_data=created)
        return _mock_response()

    with patch("httpx.request", side_effect=mock_request):
        agent_id = backend.ensure_hybrid_agent()
    assert agent_id == "new-hybrid-uuid"


def test_ensure_hybrid_agent_caches_result(backend):
    agents_data = {
        "data": [
            {"id": "cached-hybrid-id", "is_builtin": False, "config": {"agent_type": "hybrid-rag-wiki"}},
        ],
        "success": True,
    }
    with patch("httpx.request", return_value=_mock_response(json_data=agents_data)) as mock_req:
        id1 = backend.ensure_hybrid_agent()
        id2 = backend.ensure_hybrid_agent()
    assert id1 == id2 == "cached-hybrid-id"
    assert mock_req.call_count == 1


def test_ask_passes_agent_id(backend):
    session_resp = _mock_response(json_data={"data": {"id": "sess-1"}, "success": True})
    chat_sse = _MockStreamResponse(
        'event:message\ndata:{"response_type":"answer","content":"hello","done":true}\n\n'
        'event:message\ndata:{"response_type":"references","knowledge_references":[],"done":true}\n\n'
    )

    call_args = {}
    call_url = {}
    @contextmanager
    def mock_stream(method, url, **kwargs):
        if method == "POST" and "agent-chat" in url:
            call_args.update(kwargs)
            call_url["url"] = url
            yield chat_sse
        else:
            yield _MockStreamResponse("")

    def mock_request(method, url, **kwargs):
        if method == "POST" and "sessions" in url:
            return session_resp
        return _mock_response()

    with patch("httpx.request", side_effect=mock_request), patch("httpx.stream", side_effect=mock_stream):
        result, chat_id = backend.ask("kb-123", "test question", agent_id="my-agent")

    assert "agent-chat" in call_url.get("url", "")
    body = call_args.get("json", {})
    assert body.get("agent_enabled") is True
    assert body.get("agent_id") == "my-agent"
    assert body.get("web_search_enabled") is False


# ---------- ensure_agent_models ----------

def _agent_get_response(agent_id="ag-1", model_id="", rerank_model_id=""):
    """Build a GET /agents/{id} response with the given config."""
    return _mock_response(json_data={
        "success": True,
        "data": {
            "id": agent_id,
            "name": "test-agent",
            "description": "",
            "avatar": "",
            "is_builtin": False,
            "tenant_id": 10000,
            "created_by": "",
            "config": {
                "agent_mode": "quick-answer",
                "system_prompt": "preserve me",
                "model_id": model_id,
                "rerank_model_id": rerank_model_id,
                "temperature": 0.7,
                "max_iterations": 10,
            },
        },
    })


def test_ensure_agent_models_fills_empty_model_id():
    """model_id empty + backend has summary_model_id → PUT with resolved UUID."""
    backend = WeknoraBackend(
        base_url="http://localhost", api_key="k",
        summary_model_id="chat-model-uuid",
    )
    # _resolve_model_id hits /api/v1/models; bypass it by pre-populating the cache
    backend._model_name_to_id = {"chat-model-uuid": "uuid-chat-123"}

    get_resp = _agent_get_response(model_id="", rerank_model_id="")
    put_resp = _mock_response(json_data={"success": True, "data": {"id": "ag-1"}})
    captured = {}

    def mock_request(method, url, **kwargs):
        if method == "GET" and url.endswith("/api/v1/agents/ag-1"):
            return get_resp
        if method == "PUT" and url.endswith("/api/v1/agents/ag-1"):
            captured["body"] = kwargs.get("json")
            return put_resp
        return _mock_response()

    with patch("httpx.request", side_effect=mock_request):
        patched = backend.ensure_agent_models("ag-1")

    assert patched is True
    config = captured["body"]["config"]
    assert config["model_id"] == "uuid-chat-123"
    # Other fields preserved (PUT is full-overwrite, must not lose them)
    assert config["system_prompt"] == "preserve me"
    assert config["agent_mode"] == "quick-answer"
    assert config["temperature"] == 0.7
    # Top-level fields preserved
    assert captured["body"]["name"] == "test-agent"
    assert captured["body"]["is_builtin"] is False


def test_ensure_agent_models_skips_when_already_set():
    """model_id already configured → no PUT."""
    backend = WeknoraBackend(
        base_url="http://localhost", api_key="k",
        summary_model_id="chat-model-uuid",
    )
    get_resp = _agent_get_response(model_id="existing-uuid", rerank_model_id="")

    put_called = []
    def mock_request(method, url, **kwargs):
        if method == "GET" and url.endswith("/api/v1/agents/ag-1"):
            return get_resp
        if method == "PUT":
            put_called.append(True)
        return _mock_response()

    with patch("httpx.request", side_effect=mock_request):
        patched = backend.ensure_agent_models("ag-1")

    assert patched is False
    assert put_called == []


def test_ensure_agent_models_skips_when_no_summary_configured():
    """backend has no summary_model_id → can't heal model_id, skip PUT."""
    backend = WeknoraBackend(base_url="http://localhost", api_key="k")  # no summary_model_id
    get_resp = _agent_get_response(model_id="", rerank_model_id="")

    put_called = []
    def mock_request(method, url, **kwargs):
        if method == "GET" and url.endswith("/api/v1/agents/ag-1"):
            return get_resp
        if method == "PUT":
            put_called.append(True)
        return _mock_response()

    with patch("httpx.request", side_effect=mock_request):
        patched = backend.ensure_agent_models("ag-1")

    assert patched is False
    assert put_called == []


def test_ensure_agent_models_fills_rerank_when_configured():
    """rerank_model_id empty + backend has rerank config → fill it too."""
    backend = WeknoraBackend(
        base_url="http://localhost", api_key="k",
        summary_model_id="chat-uuid",
        rerank_model_id="rerank-uuid",
    )
    backend._model_name_to_id = {"chat-uuid": "uuid-chat", "rerank-uuid": "uuid-rerank"}
    get_resp = _agent_get_response(model_id="", rerank_model_id="")

    captured = {}
    def mock_request(method, url, **kwargs):
        if method == "GET" and url.endswith("/api/v1/agents/ag-1"):
            return get_resp
        if method == "PUT" and url.endswith("/api/v1/agents/ag-1"):
            captured["body"] = kwargs.get("json")
            return _mock_response(json_data={"success": True, "data": {"id": "ag-1"}})
        return _mock_response()

    with patch("httpx.request", side_effect=mock_request):
        patched = backend.ensure_agent_models("ag-1")

    assert patched is True
    config = captured["body"]["config"]
    assert config["model_id"] == "uuid-chat"
    assert config["rerank_model_id"] == "uuid-rerank"


def test_ensure_agent_models_leaves_rerank_empty_when_not_configured():
    """rerank_model_id empty + backend has no rerank config → rerank stays empty (skipped)."""
    backend = WeknoraBackend(
        base_url="http://localhost", api_key="k",
        summary_model_id="chat-uuid",
        # no rerank_model_id
    )
    backend._model_name_to_id = {"chat-uuid": "uuid-chat"}
    get_resp = _agent_get_response(model_id="", rerank_model_id="")

    captured = {}
    def mock_request(method, url, **kwargs):
        if method == "GET" and url.endswith("/api/v1/agents/ag-1"):
            return get_resp
        if method == "PUT" and url.endswith("/api/v1/agents/ag-1"):
            captured["body"] = kwargs.get("json")
            return _mock_response(json_data={"success": True, "data": {"id": "ag-1"}})
        return _mock_response()

    with patch("httpx.request", side_effect=mock_request):
        patched = backend.ensure_agent_models("ag-1")

    # model_id was filled → patched=True
    assert patched is True
    # but rerank_model_id is still "" (we didn't have a value to fill)
    assert captured["body"]["config"]["rerank_model_id"] == ""


# ---------- ask() self-heal ----------

def _sse_error_stream(message: str) -> _MockStreamResponse:
    """Build a 200 OK SSE stream carrying a single error event."""
    return _MockStreamResponse(
        'event:message\n'
        f'data:{{"response_type":"error","content":"{message}","done":true}}\n\n'
    )


def _sse_answer_stream(content: str = "healed answer") -> _MockStreamResponse:
    return _MockStreamResponse(
        'event:message\n'
        f'data:{{"response_type":"answer","content":"{content}","done":true}}\n\n'
    )


def _patch_ask_httpx(chat_responses, agent_get_resp=None, agent_put_resp=None, session_id="sess-1"):
    """Patch httpx.request + httpx.stream for an ask() self-heal test.

    chat_responses: list of _MockStreamResponse, consumed in order across agent-chat calls.
    Returns a dict tracking call counts (chat_calls, get_calls, put_calls).
    """
    state = {"chat_calls": 0, "get_calls": 0, "put_calls": 0}
    session_resp = _mock_response(json_data={"data": {"id": session_id}, "success": True})

    def mock_request(method, url, **kwargs):
        if method == "POST" and "sessions" in url:
            return session_resp
        if method == "GET" and url.endswith("/api/v1/agents/ag-1"):
            state["get_calls"] += 1
            return agent_get_resp or _mock_response()
        if method == "PUT" and url.endswith("/api/v1/agents/ag-1"):
            state["put_calls"] += 1
            return agent_put_resp or _mock_response(json_data={"success": True, "data": {"id": "ag-1"}})
        return _mock_response()

    @contextmanager
    def mock_stream(method, url, **kwargs):
        if method == "POST" and "agent-chat" in url:
            idx = state["chat_calls"]
            state["chat_calls"] += 1
            yield chat_responses[min(idx, len(chat_responses) - 1)]
        else:
            yield _MockStreamResponse("")

    return state, mock_request, mock_stream


def test_ask_self_heals_on_chat_model_not_configured():
    """First ask hits 'model_id not configured' → ensure fills it → retry succeeds."""
    backend = WeknoraBackend(
        base_url="http://localhost", api_key="k",
        summary_model_id="chat-uuid",
    )
    backend._model_name_to_id = {"chat-uuid": "uuid-chat"}

    state, mock_request, mock_stream = _patch_ask_httpx(
        chat_responses=[
            _sse_error_stream("chat model is not configured: please set model_id on agent ag-1"),
            _sse_answer_stream("healed answer"),
        ],
        agent_get_resp=_agent_get_response(model_id="", rerank_model_id=""),
    )

    with patch("httpx.request", side_effect=mock_request), patch("httpx.stream", side_effect=mock_stream):
        result, _ = backend.ask("kb-1", "q", agent_id="ag-1")

    # Retried once after healing
    assert state["chat_calls"] == 2
    assert result.answer == "healed answer"


def test_ask_friendly_error_when_rerank_cannot_be_healed():
    """rerank missing + backend has no rerank_model_id → friendly RuntimeError.

    model_id *can* be healed (backend has summary_model_id), so we heal + retry.
    The retry still fails on rerank → we surface a friendly, actionable error
    instead of the raw SSE message.
    """
    backend = WeknoraBackend(
        base_url="http://localhost", api_key="k",
        summary_model_id="chat-uuid",
        # no rerank_model_id configured
    )
    backend._model_name_to_id = {"chat-uuid": "uuid-chat"}

    state, mock_request, mock_stream = _patch_ask_httpx(
        chat_responses=[_sse_error_stream(
            "rerank model is not configured: please set rerank_model_id on the agent"
        )] * 2,  # both attempts fail on rerank
        agent_get_resp=_agent_get_response(model_id="", rerank_model_id=""),
    )

    with patch("httpx.request", side_effect=mock_request), patch("httpx.stream", side_effect=mock_stream):
        with pytest.raises(RuntimeError, match="rerank_model_id") as exc_info:
            backend.ask("kb-1", "q", agent_id="ag-1")

    # Healed (model_id filled) → retried once → still failed on rerank → friendly error.
    assert state["chat_calls"] == 2
    assert "rerank_model_id" in str(exc_info.value)
    assert "系统配置" in str(exc_info.value)


def test_ask_passes_through_non_model_errors():
    """A non-model-config error (e.g. network/500) is not self-healed."""
    backend = WeknoraBackend(
        base_url="http://localhost", api_key="k",
        summary_model_id="chat-uuid",
    )
    backend._model_name_to_id = {"chat-uuid": "uuid-chat"}

    state, mock_request, mock_stream = _patch_ask_httpx(
        chat_responses=[_sse_error_stream("something else went wrong")],
    )

    with patch("httpx.request", side_effect=mock_request), patch("httpx.stream", side_effect=mock_stream):
        with pytest.raises(RuntimeError, match="something else went wrong"):
            backend.ask("kb-1", "q", agent_id="ag-1")

    # No heal attempt, no retry
    assert state["chat_calls"] == 1
    assert state["get_calls"] == 0


def test_ask_friendly_error_when_chat_model_cannot_be_healed():
    """chat model missing + backend has no summary_model_id → friendly error, no retry."""
    backend = WeknoraBackend(base_url="http://localhost", api_key="k")  # no summary_model_id

    state, mock_request, mock_stream = _patch_ask_httpx(
        chat_responses=[_sse_error_stream(
            "chat model is not configured: please set model_id on agent ag-1"
        )],
        agent_get_resp=_agent_get_response(model_id="", rerank_model_id=""),
    )

    with patch("httpx.request", side_effect=mock_request), patch("httpx.stream", side_effect=mock_stream):
        with pytest.raises(RuntimeError, match="summary_model_id") as exc_info:
            backend.ask("kb-1", "q", agent_id="ag-1")

    # No heal possible (no summary_model_id) → no retry, friendly error
    assert state["chat_calls"] == 1
    assert "summary_model_id" in str(exc_info.value)
