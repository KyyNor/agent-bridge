"""Tests for WeknoraBackend agent management methods."""
from __future__ import annotations

import json
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
    chat_resp = MagicMock()
    chat_resp.status_code = 200
    chat_resp.text = (
        'event:message\ndata:{"response_type":"answer","content":"hello","done":true}\n\n'
        'event:message\ndata:{"response_type":"references","knowledge_references":[],"done":true}\n\n'
    )
    chat_resp.json.return_value = {"success": True}

    call_args = {}
    call_url = {}
    def capture_request(method, url, **kwargs):
        if method == "POST" and "sessions" in url:
            return session_resp
        if method == "POST" and "agent-chat" in url:
            call_args.update(kwargs)
            call_url["url"] = url
            return chat_resp
        return _mock_response()

    with patch("httpx.request", side_effect=capture_request):
        result, chat_id = backend.ask("kb-123", "test question", agent_id="my-agent")

    assert "agent-chat" in call_url.get("url", "")
    body = call_args.get("json", {})
    assert body.get("agent_enabled") is True
    assert body.get("agent_id") == "my-agent"
    assert body.get("web_search_enabled") is False
