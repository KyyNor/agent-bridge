from __future__ import annotations

from pathlib import Path

import pytest

from agent_bridge.core.config import AgentBridgePaths
from agent_bridge.storage.sqlite import SQLiteStore


@pytest.fixture
def store(tmp_path: Path) -> SQLiteStore:
    paths = AgentBridgePaths.from_root(tmp_path / "agent-bridge")
    paths.data_dir.mkdir(parents=True)
    s = SQLiteStore(paths.db_path)
    s.init_schema()
    return s


def test_init_schema_creates_all_tables(store: SQLiteStore) -> None:
    with store.connect() as conn:
        tables = {row["name"] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()}
    expected = {
        "knowledge_bases", "knowledge_base_members",
        "documents", "document_versions", "document_kbs",
        "backend_targets", "sync_jobs", "sync_states",
        "mcp_services", "mcp_tools",
        "project_profiles", "profile_source_rules", "profile_resource_rules",
        "tool_call_logs",
        "code_repositories", "codegraph_sync_runs", "codegraph_index_items",
    }
    assert expected <= tables


def test_facade_exposes_repository_properties(store: SQLiteStore) -> None:
    from agent_bridge.storage.repositories.capabilities import CapabilitiesRepository
    from agent_bridge.storage.repositories.codegraph import CodeGraphRepository
    from agent_bridge.storage.repositories.governance import GovernanceRepository
    from agent_bridge.storage.repositories.knowledge import KnowledgeRepository

    assert isinstance(store.knowledge, KnowledgeRepository)
    assert isinstance(store.capabilities, CapabilitiesRepository)
    assert isinstance(store.governance, GovernanceRepository)
    assert isinstance(store.codegraph, CodeGraphRepository)


def test_knowledge_facade_and_repository_share_db_path(store: SQLiteStore) -> None:
    kb = store.create_kb("test-kb", "Test", "desc", "root")
    assert kb["slug"] == "test-kb"

    assert store.knowledge._db_path == store.db_path


def test_capabilities_facade_and_repository_share_db_path(store: SQLiteStore) -> None:
    svc = store.create_mcp_service(service_key="svc1", name="Service 1", endpoint_url="http://localhost:8080", headers={}, description="", tags=[], created_by="root")
    assert svc["service_key"] == "svc1"

    assert store.capabilities._db_path == store.db_path


def test_governance_facade_and_repository_share_db_path(store: SQLiteStore) -> None:
    profile = store.upsert_project_profile(profile_key="prof1", name="Profile 1", description="desc", status="active", created_by="root")
    assert profile["profile_key"] == "prof1"

    assert store.governance._db_path == store.db_path


def test_codegraph_facade_and_repository_share_db_path(store: SQLiteStore) -> None:
    repo = store.upsert_code_repository(
        repo_key="repo1", name="Repo 1", git_url="https://example.com/repo.git",
        branch="main", auth_ref="", description="", tags=[], category_key="", sync_interval_minutes=60, auto_understand=False, status="active",
    )
    assert repo["repo_key"] == "repo1"

    assert store.codegraph._db_path == store.db_path
