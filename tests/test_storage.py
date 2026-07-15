from __future__ import annotations

from pathlib import Path

from agent_bridge.knowledge_management.docs_knowledge.archive import ArchiveStorage
from agent_bridge.core.config import ServerConfig, AgentBridgePaths, default_root, ensure_directories, load_server_config
from agent_bridge.core.domain import KbRole, Operation, SyncStateStatus
from agent_bridge.storage.sqlite import SQLiteStore


def test_ensure_directories_creates_default_tree(tmp_path: Path) -> None:
    paths = AgentBridgePaths.from_root(tmp_path / "agent-bridge")
    ensure_directories(paths)
    assert paths.config_dir.is_dir()
    assert paths.data_dir.is_dir()
    assert paths.archive_dir.is_dir()
    assert paths.mock_backend_dir.is_dir()
    assert paths.logs_dir.is_dir()
    assert paths.run_dir.is_dir()


def test_load_server_config_writes_default_admin(tmp_path: Path) -> None:
    paths = AgentBridgePaths.from_root(tmp_path / "agent-bridge")
    config = load_server_config(paths)
    assert config == ServerConfig(host="127.0.0.1", port=8765, admins={"root"})
    assert "admins = [\"root\"]" in paths.server_config_path.read_text()


def test_default_root_uses_environment_override(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "custom-wiki"
    monkeypatch.setenv("AGENT_BRIDGE_ROOT", str(root))

    assert default_root() == root
    assert AgentBridgePaths.from_root().root == root


def test_load_server_config_uses_default_user_for_new_admin(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_BRIDGE_USER", "kyynor")
    paths = AgentBridgePaths.from_root(tmp_path / "agent-bridge")

    config = load_server_config(paths)

    assert config.admins == {"kyynor"}
    assert "admins = [\"kyynor\"]" in paths.server_config_path.read_text()


def test_archive_store_file_by_hash(tmp_path: Path) -> None:
    paths = AgentBridgePaths.from_root(tmp_path / "agent-bridge")
    ensure_directories(paths)
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"hello wiki")
    result = ArchiveStorage(paths.archive_dir).store(source)
    assert result.content_hash == "4dc8be6383516954f9fdec2f11adc5aa0e33b04bb77b790de2a03a1e64ab75e8"
    assert result.file_size == 10
    assert result.archive_path.exists()
    assert result.archive_path.read_bytes() == b"hello wiki"


def test_archive_content_hash_matches_store_hash(tmp_path: Path) -> None:
    source = tmp_path / "guide.md"
    source.write_bytes(b"same content")
    archive = ArchiveStorage(tmp_path / "archive")

    assert archive.content_hash(source) == archive.store(source).content_hash


def test_sqlite_store_creates_kb_and_members(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    kb = store.create_kb(slug="frontend-docs", name="Frontend Docs", description="", created_by="root")
    store.grant_member(kb_id=kb["id"], linux_user="alice", role=KbRole.contributor)
    visible = store.list_kbs_for_user("alice")
    assert [item["slug"] for item in visible] == ["frontend-docs"]
    assert store.get_member_role(kb["id"], "alice") == KbRole.contributor


def test_sqlite_store_document_version_and_jobs(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    kb = store.create_kb(slug="frontend-docs", name="Frontend Docs", description="", created_by="root")
    doc = store.create_document(slug="guide", title="Guide", owner_user="alice")
    store.attach_document_to_kb(doc_id=doc["id"], kb_id=kb["id"], added_by="alice")
    version = store.create_document_version(
        doc_id=doc["id"],
        original_filename="Guide.pdf",
        content_hash="abc123",
        file_size=12,
        mime_type="application/pdf",
        archive_path="/archive/abc123.pdf",
        created_by="alice",
    )
    job = store.create_sync_job(doc_id=doc["id"], kb_id=kb["id"], operation=Operation.create, version_id=version["id"])
    assert version["version_no"] == 1
    assert job["status"] == "pending"
    assert store.list_docs_for_kb(kb_id=kb["id"])[0]["slug"] == "guide"


def test_find_current_document_by_content_hash_is_scoped_to_kb(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    kb_a = store.create_kb(slug="kb-a", name="KB A", description="", created_by="root")
    kb_b = store.create_kb(slug="kb-b", name="KB B", description="", created_by="root")
    doc = store.create_document(slug="guide", title="Guide", owner_user="root")
    store.attach_document_to_kb(doc_id=doc["id"], kb_id=kb_a["id"], added_by="root")
    store.create_document_version(
        doc_id=doc["id"],
        original_filename="guide.md",
        content_hash="hash-a",
        file_size=4,
        mime_type="text/markdown",
        archive_path="/a",
        created_by="root",
    )

    found = store.find_current_document_by_content_hash(kb_a["id"], "hash-a")
    assert found is not None
    assert found["id"] == doc["id"]
    assert found["current_version_no"] == 1
    assert store.find_current_document_by_content_hash(kb_b["id"], "hash-a") is None


def test_sqlite_store_list_document_slugs_includes_soft_deleted(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    doc = store.create_document(slug="guide", title="Guide", owner_user="alice")

    store.soft_delete_document(doc["id"])

    assert "guide" in store.list_document_slugs()


def test_purge_document_only_returns_archive_paths_no_longer_referenced(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    kb = store.create_kb(slug="frontend-docs", name="Frontend Docs", description="", created_by="root")
    first = store.create_document(slug="guide-a", title="Guide A", owner_user="alice")
    second = store.create_document(slug="guide-b", title="Guide B", owner_user="alice")
    store.attach_document_to_kb(first["id"], kb["id"], added_by="alice")
    store.attach_document_to_kb(second["id"], kb["id"], added_by="alice")
    archive_path = str(wm_paths.archive_dir / "shared.pdf")
    for doc in (first, second):
        store.create_document_version(
            doc_id=doc["id"],
            original_filename="Guide.pdf",
            content_hash="abc123",
            file_size=12,
            mime_type="application/pdf",
            archive_path=archive_path,
            created_by="alice",
        )

    assert store.purge_document(first["id"]) == []
    assert store.list_versions(second["id"])[0]["archive_path"] == archive_path
    assert store.purge_document(second["id"]) == [archive_path]


def test_create_document_records_source(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    # 默认值(不传 source 参数)
    doc = store.create_document(slug="manual", title="Manual", owner_user="alice")
    assert doc["source_type"] == "manual"
    assert doc["source_repo_key"] == ""
    # git 来源
    git_doc = store.create_document(
        slug="guide", title="Guide", owner_user="alice",
        source_type="git", source_repo_key="docs-repo",
    )
    assert git_doc["source_type"] == "git"
    assert git_doc["source_repo_key"] == "docs-repo"


def test_list_git_docs_for_repo(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    kb = store.create_kb(slug="docs", name="Docs", description="", created_by="root")
    # 两个 active git 文档 + 一个 manual 文档 + 一个已软删的 git 文档
    g1 = store.create_document("guide", "Guide", "root", source_type="git", source_repo_key="r1")
    g2 = store.create_document("notes", "Notes", "root", source_type="git", source_repo_key="r1")
    manual = store.create_document("manual", "Manual", "root")  # 不属于任何 repo
    g_del = store.create_document("old", "Old", "root", source_type="git", source_repo_key="r1")
    for d in (g1, g2, g_del, manual):
        store.attach_document_to_kb(d["id"], kb["id"], "root")
    store.soft_delete_document(g_del["id"])
    # 为 g1 建 version 带 content_hash
    store.create_document_version(
        doc_id=g1["id"], original_filename="guide.md", content_hash="hash-a",
        file_size=8, mime_type="text/markdown", archive_path="/a", created_by="root",
    )

    result = store.list_git_docs_for_repo(kb["id"], "r1")
    slugs = {d["slug"] for d in result}
    assert slugs == {"guide", "notes"}  # 不含 manual、不含已软删的 old
    guide = next(d for d in result if d["slug"] == "guide")
    assert guide["content_hash"] == "hash-a"


def test_list_all_active_repo_sources(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    kb1 = store.create_kb(slug="kb1", name="KB1", description="", created_by="root")
    kb2 = store.create_kb(slug="kb2", name="KB2", description="", created_by="root")
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO code_repositories (repo_key, name, git_url, branch, auth_ref, status) "
            "VALUES ('r1', 'R1', 'http://x', 'main', '', 'active')"
        )
    store.upsert_kb_repo_source(kb1["id"], "r1", [".md"])
    store.upsert_kb_repo_source(kb2["id"], "r1", [".md", ".txt"])
    result = store.list_all_active_repo_sources()
    assert len(result) == 2
    pairs = {(r["kb_slug"], r["repo_key"]) for r in result}
    assert ("kb1", "r1") in pairs
    assert ("kb2", "r1") in pairs
    assert all(r["status"] == "active" for r in result)


def test_list_kb_repo_sources_includes_doc_count(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    kb = store.create_kb(slug="docs", name="Docs", description="", created_by="root")
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO code_repositories (repo_key, name, git_url, branch, auth_ref, status) "
            "VALUES ('r1', 'R1', 'http://x', 'main', '', 'active')"
        )
    store.upsert_kb_repo_source(kb["id"], "r1", [".md"])
    # 两个 git 文档 + 一个 manual
    for slug in ("g1", "g2"):
        d = store.create_document(slug, slug, "root", source_type="git", source_repo_key="r1")
        store.attach_document_to_kb(d["id"], kb["id"], "root")
    manual = store.create_document("m1", "M1", "root")
    store.attach_document_to_kb(manual["id"], kb["id"], "root")
    result = store.list_kb_repo_sources(kb["id"])
    assert result[0]["doc_count"] == 2


def test_delete_kb_repo_source_soft_deletes(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    kb = store.create_kb(slug="docs", name="Docs", description="", created_by="root")
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO code_repositories (repo_key, name, git_url, branch, auth_ref, status) "
            "VALUES ('r1', 'R1', 'http://x', 'main', '', 'active')"
        )
    store.upsert_kb_repo_source(kb["id"], "r1", [".md"])
    store.delete_kb_repo_source(kb["id"], "r1")
    # list 只返回 active
    assert store.list_kb_repo_sources(kb["id"]) == []
    # 但行还在(软删)
    with store.connect() as conn:
        row = conn.execute(
            "SELECT status FROM kb_repo_sources WHERE kb_id=? AND repo_key='r1'", (kb["id"],)
        ).fetchone()
        assert row[0] == "inactive"


def test_schema_migration_adds_phase2_columns(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "test.db")
    store.init_schema()
    kb = store.create_kb(slug="test-kb", name="Test KB", description="", created_by="root")
    doc = store.create_document(slug="test-doc", title="Test Doc", owner_user="root")
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO sync_states (doc_id, kb_id, backend_slug, backend_doc_id, status) VALUES (?, ?, 'mock', 'doc1', 'synced')",
            (doc["id"], kb["id"]),
        )
    store.migrate_phase2()
    with store.connect() as conn:
        row = conn.execute("SELECT * FROM sync_states WHERE backend_slug = 'mock'").fetchone()
        assert row is not None
        assert row["status"] == "synced"
        assert row["backend_status"] is None
        assert row["chunk_count"] is None
        assert row["progress"] is None
        assert row["backend_error"] is None
    with store.connect() as conn:
        col_names = {desc[1] for desc in conn.execute("PRAGMA table_info(backend_targets)").fetchall()}
        assert "backend_kb_id" in col_names


def test_migration_is_idempotent(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "test.db")
    store.init_schema()
    store.migrate_phase2()
    store.migrate_phase2()


def test_list_backend_targets_for_kb(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "test.db")
    store.init_schema()
    kb = store.create_kb("test-kb", "Test", "", "root")
    store.ensure_backend_target(kb["id"], slug="mock", backend_type="mock")
    store.ensure_backend_target(kb["id"], slug="ragflow", backend_type="ragflow")
    targets = store.list_backend_targets(kb["id"])
    assert len(targets) == 2
    slugs = {t["slug"] for t in targets}
    assert slugs == {"mock", "ragflow"}


def test_set_backend_target_inactive(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "test.db")
    store.init_schema()
    kb = store.create_kb("test-kb", "Test", "", "root")
    store.ensure_backend_target(kb["id"], slug="mock", backend_type="mock")
    store.set_backend_target_status(kb["id"], "mock", "inactive")
    targets = store.list_backend_targets(kb["id"])
    mock_target = next(t for t in targets if t["slug"] == "mock")
    assert mock_target["status"] == "inactive"


def test_list_sync_states_for_doc(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "test.db")
    store.init_schema()
    kb = store.create_kb("test-kb", "Test", "", "root")
    doc = store.create_document("test-doc", "Test Doc", "root")
    store.attach_document_to_kb(doc["id"], kb["id"], "root")
    store.upsert_sync_state(doc["id"], kb["id"], "mock", "mock:doc", SyncStateStatus.synced)
    store.upsert_sync_state(doc["id"], kb["id"], "ragflow", "rf:doc", SyncStateStatus.synced)
    states = store.list_sync_states_for_doc(doc["id"])
    assert len(states) == 2
    assert {s["backend_slug"] for s in states} == {"mock", "ragflow"}


def test_update_backend_target_kb_id(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "test.db")
    store.init_schema()
    kb = store.create_kb("test-kb", "Test", "", "root")
    store.ensure_backend_target(kb["id"], slug="ragflow", backend_type="ragflow")
    store.update_backend_target_kb_id(kb["id"], "ragflow", "rf-dataset-123")
    targets = store.list_backend_targets(kb["id"])
    ragflow_target = next(t for t in targets if t["slug"] == "ragflow")
    assert ragflow_target["backend_kb_id"] == "rf-dataset-123"


def test_update_backend_target_config(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "test.db")
    store.init_schema()
    kb = store.create_kb(slug="test-kb", name="Test", description="", created_by="root")
    store.ensure_backend_target(kb["id"], slug="ragflow", backend_type="ragflow")

    store.update_backend_target_config(kb["id"], "ragflow", {"chat_id": "chat-123"})

    targets = store.list_backend_targets(kb["id"])
    ragflow_target = next(t for t in targets if t["slug"] == "ragflow")
    assert ragflow_target["config_json"] == '{"chat_id": "chat-123"}'


def test_update_backend_target_config_merges_existing(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "test.db")
    store.init_schema()
    kb = store.create_kb(slug="test-kb", name="Test", description="", created_by="root")
    store.ensure_backend_target(kb["id"], slug="ragflow", backend_type="ragflow")
    store.update_backend_target_config(kb["id"], "ragflow", {"chat_id": "chat-123"})

    store.update_backend_target_config(kb["id"], "ragflow", {"extra": "value"})

    targets = store.list_backend_targets(kb["id"])
    ragflow_target = next(t for t in targets if t["slug"] == "ragflow")
    import json
    config = json.loads(ragflow_target["config_json"])
    assert config["chat_id"] == "chat-123"
    assert config["extra"] == "value"
