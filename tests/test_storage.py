from __future__ import annotations

from pathlib import Path

from wiki_manager.archive import ArchiveStorage
from wiki_manager.config import ServerConfig, WikiManagerPaths, ensure_directories, load_server_config
from wiki_manager.domain import KbRole, Operation, SyncStateStatus
from wiki_manager.storage import SQLiteStore


def test_ensure_directories_creates_default_tree(tmp_path: Path) -> None:
    paths = WikiManagerPaths.from_root(tmp_path / "wiki-manager")
    ensure_directories(paths)
    assert paths.config_dir.is_dir()
    assert paths.data_dir.is_dir()
    assert paths.archive_dir.is_dir()
    assert paths.mock_backend_dir.is_dir()
    assert paths.logs_dir.is_dir()
    assert paths.run_dir.is_dir()


def test_load_server_config_writes_default_admin(tmp_path: Path) -> None:
    paths = WikiManagerPaths.from_root(tmp_path / "wiki-manager")
    config = load_server_config(paths)
    assert config == ServerConfig(host="127.0.0.1", port=8765, admins={"root"})
    assert "admins = [\"root\"]" in paths.server_config_path.read_text()


def test_archive_store_file_by_hash(tmp_path: Path) -> None:
    paths = WikiManagerPaths.from_root(tmp_path / "wiki-manager")
    ensure_directories(paths)
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"hello wiki")
    result = ArchiveStorage(paths.archive_dir).store(source)
    assert result.content_hash == "4dc8be6383516954f9fdec2f11adc5aa0e33b04bb77b790de2a03a1e64ab75e8"
    assert result.file_size == 10
    assert result.archive_path.exists()
    assert result.archive_path.read_bytes() == b"hello wiki"


def test_sqlite_store_creates_kb_and_members(wm_paths: WikiManagerPaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    kb = store.create_kb(slug="frontend-docs", name="Frontend Docs", description="", created_by="root")
    store.grant_member(kb_id=kb["id"], linux_user="alice", role=KbRole.contributor)
    visible = store.list_kbs_for_user("alice")
    assert [item["slug"] for item in visible] == ["frontend-docs"]
    assert store.get_member_role(kb["id"], "alice") == KbRole.contributor


def test_sqlite_store_document_version_and_jobs(wm_paths: WikiManagerPaths) -> None:
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


def test_sqlite_store_list_document_slugs_includes_soft_deleted(wm_paths: WikiManagerPaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    doc = store.create_document(slug="guide", title="Guide", owner_user="alice")

    store.soft_delete_document(doc["id"])

    assert "guide" in store.list_document_slugs()


def test_purge_document_only_returns_archive_paths_no_longer_referenced(wm_paths: WikiManagerPaths) -> None:
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
