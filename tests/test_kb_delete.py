"""TDD tests for knowledge base (KB) deletion.

Contract (per the approved plan):
- admin can delete a KB only after it has no active documents
- deleting a KB purges governance resource rules referencing it
- non-admin is denied; missing KB raises NotFound
- deleting clears the KB row (FK CASCADE removes members/targets/sync rows)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_bridge.app.service import AgentBridgeService
from agent_bridge.core.config import BackendConfig, AgentBridgePaths, ensure_directories
from agent_bridge.core.domain import AccessDenied, NotFound, ValidationError
from agent_bridge.knowledge_management.docs_knowledge.backends.registry import BackendRegistry


def _service(wm_paths: AgentBridgePaths, tmp_path: Path) -> AgentBridgeService:
    ensure_directories(wm_paths)
    service = AgentBridgeService.create(wm_paths, admins={"root"})
    service.registry = BackendRegistry(
        {"mock": BackendConfig(slug="mock", backend_type="mock")},
        paths=tmp_path,
    )
    service.init_system()
    return service


def test_delete_kb_requires_admin(wm_paths, tmp_path: Path) -> None:
    service = _service(wm_paths, tmp_path)
    service.create_kb("root", "frontend", "Frontend", "")
    with pytest.raises(AccessDenied):
        service.delete_kb("alice", "frontend")


def test_delete_kb_missing_raises_not_found(wm_paths, tmp_path: Path) -> None:
    service = _service(wm_paths, tmp_path)
    with pytest.raises(NotFound):
        service.delete_kb("root", "missing")


def test_delete_kb_refused_when_documents_remain(wm_paths, tmp_path: Path) -> None:
    service = _service(wm_paths, tmp_path)
    service.create_kb("root", "frontend", "Frontend", "")
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"one")
    service.add_document("root", source, ["frontend"], later=True)

    with pytest.raises(ValidationError, match="请先删除"):
        service.delete_kb("root", "frontend")
    # KB still exists
    assert service.store.get_kb_by_slug("frontend") is not None


def test_delete_kb_removes_kb_and_cleans_resource_rules(wm_paths, tmp_path: Path) -> None:
    service = _service(wm_paths, tmp_path)
    service.create_kb("root", "frontend", "Frontend", "")
    # seed a governance resource rule referencing this KB
    service.governance.upsert_profile("root", "dev", "Dev", "", "active")
    service.governance.replace_profile_resource_rules(
        "root",
        "dev",
        [{"resource_type": "wiki_kb", "resource_key": "frontend"}],
    )
    assert any(
        r["resource_key"] == "frontend"
        for r in service.store.list_profile_resource_rules("dev")
    )

    service.delete_kb("root", "frontend")

    assert service.store.get_kb_by_slug("frontend") is None
    # resource rules referencing the KB are gone
    assert not any(
        r["resource_key"] == "frontend"
        for r in service.store.list_profile_resource_rules("dev")
    )


def test_delete_kb_cascades_backend_targets(wm_paths, tmp_path: Path) -> None:
    service = _service(wm_paths, tmp_path)
    kb = service.create_kb("root", "frontend", "Frontend", "")
    # align_backends (in init_system) creates backend targets for configured backends
    targets_before = service.store.list_backend_targets(kb["id"])
    assert len(targets_before) >= 1

    service.delete_kb("root", "frontend")

    # backend_targets rows are FK CASCADE-deleted with the KB
    assert service.store.list_backend_targets(kb["id"]) == []
