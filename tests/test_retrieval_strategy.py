"""Tests for retrieval strategy resolution."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_bridge.core.config import AgentBridgePaths, BackendConfig, ensure_directories
from agent_bridge.core.domain import KbRole
from agent_bridge.knowledge_management.docs_knowledge.backends.registry import BackendRegistry
from agent_bridge.app.service import AgentBridgeService


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


def test_resolve_strategy_uses_kb_defaults(wm_paths, tmp_path):
    svc = _service(wm_paths, tmp_path)
    svc.create_kb("root", "test-kb", "Test", "")
    svc.store.update_kb_defaults(
        kb_id=svc.store.get_kb_by_slug("test-kb")["id"],
        default_backend_slug="weknora",
        default_agent_id="builtin-smart-reasoning",
    )
    kb, strategy = svc.resolve_retrieval_strategy("test-kb", profile_key=None)
    assert strategy.backend_slug == "weknora"
    assert strategy.agent_id == "builtin-smart-reasoning"


def test_resolve_strategy_profile_overrides_kb_default(wm_paths, tmp_path):
    svc = _service(wm_paths, tmp_path)
    svc.create_kb("root", "test-kb", "Test", "")
    kb_id = svc.store.get_kb_by_slug("test-kb")["id"]
    svc.store.update_kb_defaults(kb_id=kb_id, default_backend_slug="weknora", default_agent_id="hybrid-rag-wiki")
    svc.governance.upsert_profile("root", "prof-a", "Profile A", "desc", "active")
    svc.store.replace_resource_rule_profiles(
        resource_type="wiki_kb", resource_key="test-kb",
        profile_keys=["prof-a"],
        overrides={"prof-a": {"retrieval_backend_slug": "ragflow", "retrieval_agent_id": None}},
    )
    kb, strategy = svc.resolve_retrieval_strategy("test-kb", profile_key="prof-a")
    assert strategy.backend_slug == "ragflow"
    assert strategy.agent_id is None


def test_resolve_strategy_profile_partial_override_falls_back(wm_paths, tmp_path):
    svc = _service(wm_paths, tmp_path)
    svc.create_kb("root", "test-kb", "Test", "")
    kb_id = svc.store.get_kb_by_slug("test-kb")["id"]
    svc.store.update_kb_defaults(kb_id=kb_id, default_backend_slug="weknora", default_agent_id="hybrid-rag-wiki")
    svc.governance.upsert_profile("root", "prof-a", "Profile A", "desc", "active")
    svc.store.replace_resource_rule_profiles(
        resource_type="wiki_kb", resource_key="test-kb",
        profile_keys=["prof-a"],
        overrides={"prof-a": {"retrieval_backend_slug": None, "retrieval_agent_id": "builtin-wiki-researcher"}},
    )
    kb, strategy = svc.resolve_retrieval_strategy("test-kb", profile_key="prof-a")
    assert strategy.backend_slug == "weknora"  # fell back to KB default
    assert strategy.agent_id == "builtin-wiki-researcher"  # override applied


def test_resolve_strategy_no_defaults_uses_first_active(wm_paths, tmp_path):
    svc = _service(wm_paths, tmp_path)
    svc.create_kb("root", "test-kb", "Test", "")
    kb, strategy = svc.resolve_retrieval_strategy("test-kb", profile_key=None)
    assert strategy.backend_slug in ("weknora", "ragflow")


def test_search_uses_kb_default_backend(wm_paths, tmp_path, monkeypatch):
    svc = _service(wm_paths, tmp_path)
    kb = svc.create_kb("root", "test-kb", "Test", "")
    svc.store.update_kb_defaults(kb["id"], default_backend_slug="weknora", default_agent_id=None)
    resolved_backends: list[str | None] = []

    class RecordingAdapter:
        def retrieve(self, backend_kb_id: str, question: str, top_k: int = 6):
            return []

    def resolve_target(kb: dict, backend_slug: str | None) -> dict:
        resolved_backends.append(backend_slug)
        return {"slug": backend_slug, "backend_kb_id": "remote-test-kb"}

    monkeypatch.setattr(svc, "_resolve_retrieval_target", resolve_target)
    monkeypatch.setattr(svc, "_get_adapter", lambda slug: RecordingAdapter())

    svc.search("root", "test-kb", "查询")

    assert resolved_backends == ["weknora"]


def test_search_uses_profile_backend_override(wm_paths, tmp_path, monkeypatch):
    svc = _service(wm_paths, tmp_path)
    kb = svc.create_kb("root", "test-kb", "Test", "")
    svc.store.update_kb_defaults(kb["id"], default_backend_slug="weknora", default_agent_id=None)
    svc.governance.upsert_profile("root", "prof-a", "Profile A", "desc", "active")
    svc.store.replace_resource_rule_profiles(
        resource_type="wiki_kb", resource_key="test-kb",
        profile_keys=["prof-a"],
        overrides={"prof-a": {"retrieval_backend_slug": "ragflow", "retrieval_agent_id": None}},
    )
    resolved_backends: list[str | None] = []

    class RecordingAdapter:
        def retrieve(self, backend_kb_id: str, question: str, top_k: int = 6):
            return []

    def resolve_target(kb: dict, backend_slug: str | None) -> dict:
        resolved_backends.append(backend_slug)
        return {"slug": backend_slug, "backend_kb_id": "remote-test-kb"}

    monkeypatch.setattr(svc, "_resolve_retrieval_target", resolve_target)
    monkeypatch.setattr(svc, "_get_adapter", lambda slug: RecordingAdapter())

    svc.search("root", "test-kb", "查询", profile_key="prof-a")

    assert resolved_backends == ["ragflow"]


def test_update_kb_defaults_via_service(wm_paths, tmp_path):
    svc = _service(wm_paths, tmp_path)
    svc.create_kb("root", "test-kb", "Test", "")
    svc.update_kb_defaults("root", "test-kb", default_backend_slug="weknora", default_agent_id="hybrid-rag-wiki")
    kb = svc.store.get_kb_by_slug("test-kb")
    assert kb["default_backend_slug"] == "weknora"
    assert kb["default_agent_id"] == "hybrid-rag-wiki"
