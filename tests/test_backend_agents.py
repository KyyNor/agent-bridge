"""Tests for the backend agent listing/creation service methods (Requirement 2)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_bridge.core.config import AgentBridgePaths, BackendConfig, ensure_directories
from agent_bridge.core.domain import AccessDenied, NotFound, ValidationError
from agent_bridge.knowledge_management.docs_knowledge.backends.registry import BackendRegistry
from agent_bridge.app.service import AgentBridgeService


AGENTS_PAYLOAD = {
    "data": [
        {"id": "builtin-smart-reasoning", "name": "Smart Reasoning", "is_builtin": True,
         "config": {"agent_type": "smart-reasoning"}},
        {"id": "existing-hybrid-id", "name": "混合检索", "is_builtin": False,
         "config": {"agent_type": "hybrid-rag-wiki"}},
    ],
    "success": True,
}

PRESETS_PAYLOAD = {
    "data": [
        {"id": "hybrid-rag-wiki",
         "config": {"system_prompt_id": "hybrid_rag_wiki_agent"},
         "i18n": {"zh-CN": {"description": "混合检索智能体"}}},
    ],
    "success": True,
}

CREATED_PAYLOAD = {
    "data": {"id": "new-uuid", "name": "My Agent", "is_builtin": False,
             "config": {"agent_type": "hybrid-rag-wiki"}},
    "success": True,
}


def _mock_response(status_code=200, json_data=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text or json.dumps(json_data or {})
    resp.json.return_value = json_data or {}
    return resp


def _route_request(captured: dict | None = None):
    """Route httpx calls like WeknoraBackend would see them."""
    def mock_request(method, url, **kwargs):
        if captured is not None:
            captured["method"] = method
            captured["url"] = url
            captured.update(kwargs)
        if method == "GET" and url.endswith("/api/v1/agents/type-presets"):
            return _mock_response(json_data=PRESETS_PAYLOAD)
        if method == "GET" and url.endswith("/api/v1/agents"):
            return _mock_response(json_data=AGENTS_PAYLOAD)
        if method == "POST" and url.endswith("/api/v1/agents"):
            return _mock_response(json_data=CREATED_PAYLOAD)
        return _mock_response()
    return mock_request


def _service(wm_paths: AgentBridgePaths, tmp_path: Path) -> AgentBridgeService:
    ensure_directories(wm_paths)
    service = AgentBridgeService.create(wm_paths, admins={"root"})
    service.registry = BackendRegistry(
        {
            "weknora": BackendConfig(slug="weknora", backend_type="weknora", base_url="http://localhost", api_key="test"),
            "ragflow": BackendConfig(slug="ragflow", backend_type="ragflow", base_url="http://localhost", api_key="test"),
        },
        paths=tmp_path,
    )
    service.init_system()
    return service


def test_list_backend_agents_normalizes(wm_paths, tmp_path):
    svc = _service(wm_paths, tmp_path)
    with patch("httpx.request", side_effect=_route_request()):
        agents = svc.list_backend_agents("root", "weknora")
    assert [a["agent_id"] for a in agents] == ["builtin-smart-reasoning", "existing-hybrid-id"]
    hybrid = agents[1]
    assert hybrid["name"] == "混合检索"
    assert hybrid["agent_type"] == "hybrid-rag-wiki"
    assert hybrid["is_builtin"] is False


def test_list_backend_agents_non_weknora_returns_empty(wm_paths, tmp_path):
    svc = _service(wm_paths, tmp_path)
    assert svc.list_backend_agents("root", "ragflow") == []


def test_list_backend_agents_swallows_backend_errors(wm_paths, tmp_path):
    svc = _service(wm_paths, tmp_path)
    with patch("httpx.request", side_effect=RuntimeError("boom")):
        assert svc.list_backend_agents("root", "weknora") == []


def test_list_backend_agent_types_normalizes(wm_paths, tmp_path):
    svc = _service(wm_paths, tmp_path)
    with patch("httpx.request", side_effect=_route_request()):
        presets = svc.list_backend_agent_types("root", "weknora")
    assert len(presets) == 1
    assert presets[0]["preset_id"] == "hybrid-rag-wiki"
    assert presets[0]["description"] == "混合检索智能体"


def test_create_backend_agent_looks_up_preset_and_passes_full_config(wm_paths, tmp_path):
    svc = _service(wm_paths, tmp_path)
    captured: dict = {}
    with patch("httpx.request", side_effect=_route_request(captured)):
        created = svc.create_backend_agent("root", "weknora", "My Agent", "hybrid-rag-wiki")
    assert created["agent_id"] == "new-uuid"
    assert created["name"] == "My Agent"
    # Correction #1: the full preset config is forwarded to Weknora, not the preset id
    body = captured.get("json", {})
    assert body["name"] == "My Agent"
    assert body["config"] == PRESETS_PAYLOAD["data"][0]["config"]
    assert "preset_id" not in body


def test_create_backend_agent_unknown_preset_raises(wm_paths, tmp_path):
    svc = _service(wm_paths, tmp_path)
    with patch("httpx.request", side_effect=_route_request()):
        with pytest.raises(NotFound):
            svc.create_backend_agent("root", "weknora", "x", "no-such-preset")


def test_create_backend_agent_non_weknora_raises(wm_paths, tmp_path):
    svc = _service(wm_paths, tmp_path)
    with pytest.raises(ValidationError):
        svc.create_backend_agent("root", "ragflow", "x", "hybrid-rag-wiki")


@pytest.mark.parametrize("method", ["list_backend_agents", "list_backend_agent_types"])
def test_agent_listing_requires_admin(wm_paths, tmp_path, method):
    svc = _service(wm_paths, tmp_path)
    with pytest.raises(AccessDenied):
        getattr(svc, method)("nonadmin", "weknora")


def test_create_backend_agent_requires_admin(wm_paths, tmp_path):
    svc = _service(wm_paths, tmp_path)
    with pytest.raises(AccessDenied):
        svc.create_backend_agent("nonadmin", "weknora", "x", "hybrid-rag-wiki")
