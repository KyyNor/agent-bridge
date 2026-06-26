"""TDD tests for code repository deletion.

Contract (per the approved plan):
- admin can delete a code repo -> repo row + sync runs + index items gone (FK CASCADE)
- local clone directory is removed
- governance resource rules referencing the repo are purged
- non-admin denied; missing repo raises NotFound
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_bridge.app.service import AgentBridgeService
from agent_bridge.knowledge_management.code_knowledge.service import CodeGraphService
from agent_bridge.capability_hub.models import ProfileResourceType
from agent_bridge.core.config import AgentBridgePaths, ensure_directories
from agent_bridge.core.domain import AccessDenied, NotFound
from agent_bridge.storage.sqlite import SQLiteStore


def _service(wm_paths: AgentBridgePaths) -> CodeGraphService:
    ensure_directories(wm_paths)
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    return CodeGraphService(paths=wm_paths, store=store, admins={"root"})


def _upsert_repo(service: CodeGraphService, repo_key: str = "web-app") -> None:
    service.upsert_repository(
        actor="root",
        repo_key=repo_key,
        name="Web App",
        git_url="https://example.test/repo.git",
        branch="main",
        auth_ref="",
        description="Demo app",
        tags=["python"],
        category_key="",
        sync_interval_minutes=60,
        auto_understand=False,
        status="active",
    )


def test_delete_repository_requires_admin(wm_paths: AgentBridgePaths) -> None:
    service = _service(wm_paths)
    _upsert_repo(service)
    with pytest.raises(AccessDenied):
        service.delete_repository("alice", "web-app")


def test_delete_repository_missing_raises_not_found(wm_paths: AgentBridgePaths) -> None:
    service = _service(wm_paths)
    with pytest.raises(NotFound):
        service.delete_repository("root", "missing")


def test_delete_repository_removes_repo_rows_and_local_clone(wm_paths: AgentBridgePaths) -> None:
    service = _service(wm_paths)
    _upsert_repo(service)
    # simulate a local clone + an index/sync run
    local_clone = wm_paths.repos_dir / "web-app"
    local_clone.mkdir(parents=True, exist_ok=True)
    (local_clone / "app.py").write_text("x = 1", encoding="utf-8")
    service.store.create_codegraph_sync_run("web-app", status="succeeded", stage="done")

    service.delete_repository("root", "web-app")

    assert service.store.get_code_repository("web-app") is None
    assert not local_clone.exists()
    with service.store.connect() as conn:
        runs = conn.execute(
            "SELECT COUNT(*) AS c FROM codegraph_sync_runs WHERE repo_key = ?", ("web-app",)
        ).fetchone()["c"]
        items = conn.execute(
            "SELECT COUNT(*) AS c FROM codegraph_index_items WHERE repo_key = ?", ("web-app",)
        ).fetchone()["c"]
    assert runs == 0
    assert items == 0


def test_delete_repository_cleans_governance_resource_rules(wm_paths: AgentBridgePaths) -> None:
    service = _service(wm_paths)
    _upsert_repo(service)
    # seed a governance resource rule referencing this repo
    full = AgentBridgeService.create(wm_paths, admins={"root"})
    full.init_system()
    full.governance.upsert_profile("root", "dev", "Dev", "", "active")
    full.governance.replace_profile_resource_rules(
        "root",
        "dev",
        [{"resource_type": ProfileResourceType.code_repo.value, "resource_key": "web-app"}],
    )
    assert any(
        r["resource_key"] == "web-app"
        for r in service.store.list_profile_resource_rules("dev")
    )

    service.delete_repository("root", "web-app")

    assert not any(
        r["resource_key"] == "web-app"
        for r in service.store.list_profile_resource_rules("dev")
    )
