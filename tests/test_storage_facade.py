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
        "code_repositories", "codegraph_sync_runs",
    }
    assert expected <= tables
    assert "codegraph_index_items" not in tables


def test_init_schema_removes_legacy_codegraph_text_index(tmp_path: Path) -> None:
    paths = AgentBridgePaths.from_root(tmp_path / "legacy")
    paths.data_dir.mkdir(parents=True)
    store = SQLiteStore(paths.db_path)
    store.init_schema()
    store.upsert_code_repository(
        repo_key="repo1", name="Repo 1", git_url="https://example.test/repo.git",
        branch="main", auth_ref="", description="", tags=[], category_key="",
        sync_interval_minutes=60, auto_understand=False, status="active",
    )
    with store.connect() as conn:
        conn.execute(
            """
            CREATE TABLE codegraph_index_items (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              repo_key TEXT NOT NULL,
              item_type TEXT NOT NULL,
              path TEXT NOT NULL,
              content TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            "INSERT INTO codegraph_index_items (repo_key, item_type, path, content) VALUES (?, ?, ?, ?)",
            ("repo1", "file", "app.py", "print('legacy')"),
        )
        conn.execute(
            "UPDATE code_repositories SET last_commit = 'abc', last_synced_at = CURRENT_TIMESTAMP WHERE repo_key = 'repo1'"
        )

    store.init_schema()

    with store.connect() as conn:
        legacy_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'codegraph_index_items'"
        ).fetchone()
    repo = store.get_code_repository("repo1")
    assert legacy_table is None
    assert repo is not None
    assert repo["last_commit"] is None
    assert repo["last_synced_at"] is None
    assert repo["last_error"] == "CodeGraph 索引后端已统一，请重新同步仓库"


def test_migrate_phase2_creates_profile_doc_cache_for_existing_db(store: SQLiteStore) -> None:
    with store.connect() as conn:
        conn.execute("DROP TABLE profile_doc_cache")

    store.migrate_phase2()

    with store.connect() as conn:
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='profile_doc_cache'"
        ).fetchone()
    assert table is not None


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
