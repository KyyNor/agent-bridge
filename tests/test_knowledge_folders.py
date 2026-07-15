from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent_bridge.core.domain import (
    ConflictError,
    NotFound,
    Operation,
    SyncStateStatus,
    ValidationError,
)
from agent_bridge.storage.sqlite import SQLiteStore


def test_new_kb_has_one_virtual_root_with_empty_display_path(wm_paths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()

    kb = store.create_kb("docs", "Documentation", "", "root")

    root = store.get_root_folder(kb["id"])
    assert root is not None
    assert root["kb_id"] == kb["id"]
    assert root["parent_id"] is None
    assert root["is_root"] == 1
    assert root["path"] == ""
    assert len(store.list_folder_tree(kb["id"])) == 1

    store.ensure_root_folder(kb["id"])
    with store.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM knowledge_folders WHERE kb_id = ? AND is_root = 1",
            (kb["id"],),
        ).fetchone()[0] == 1


def test_old_database_migration_backfills_active_placements_and_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "old.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE knowledge_bases (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              slug TEXT NOT NULL UNIQUE,
              name TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT 'active',
              created_by TEXT NOT NULL,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE documents (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              slug TEXT NOT NULL UNIQUE,
              title TEXT NOT NULL,
              owner_user TEXT NOT NULL,
              current_version_id INTEGER,
              status TEXT NOT NULL DEFAULT 'active',
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              deleted_at TEXT
            );
            CREATE TABLE document_kbs (
              doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
              kb_id INTEGER NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
              added_by TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'active',
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              deleted_at TEXT,
              PRIMARY KEY (doc_id, kb_id)
            );
            INSERT INTO knowledge_bases (slug, name, created_by) VALUES ('legacy', 'Legacy', 'root');
            INSERT INTO documents (slug, title, owner_user) VALUES ('legacy-doc', 'Legacy document', 'root');
            INSERT INTO document_kbs (doc_id, kb_id, added_by) VALUES (1, 1, 'root');
            """
        )

    store = SQLiteStore(db_path)
    store.init_schema()
    store.migrate_phase2()
    store.migrate_phase2()

    with store.connect() as conn:
        roots = conn.execute(
            "SELECT id, parent_id, is_root FROM knowledge_folders WHERE kb_id = 1"
        ).fetchall()
        placement = conn.execute(
            "SELECT folder_id FROM document_kbs WHERE doc_id = 1 AND kb_id = 1"
        ).fetchone()
        assert {row["is_root"] for row in roots} == {1}
        assert len(roots) == 1
        assert placement["folder_id"] == roots[0]["id"]
        placement_columns = {row[1] for row in conn.execute("PRAGMA table_info(document_kbs)")}
        assert {"folder_id", "archive_entry_id"} <= placement_columns
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'knowledge_archive_entries'"
        ).fetchone() is not None

    assert store.get_document_by_slug("legacy-doc")["slug"] == "legacy-doc"


def test_folder_names_are_valid_and_siblings_are_unique(wm_paths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    kb = store.create_kb("docs", "Docs", "", "root")
    root = store.get_root_folder(kb["id"])

    folder = store.create_folder(kb["id"], root["id"], "Guides")
    with pytest.raises(ConflictError):
        store.create_folder(kb["id"], root["id"], "Guides")

    for invalid in ("", "  ", ".", "..", "a/b", "a\\b", "a\x00b", "a\n"):
        with pytest.raises(ValidationError):
            store.create_folder(kb["id"], root["id"], invalid)

    with pytest.raises(ValidationError):
        store.rename_folder(kb["id"], root["id"], "New root")
    with pytest.raises(ValidationError):
        store.move_folder(kb["id"], root["id"], folder["id"])
    with pytest.raises(ValidationError):
        store.delete_folder_subtree(kb["id"], root["id"])


def test_cross_kb_and_cyclic_folder_operations_are_rejected(wm_paths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    kb_a = store.create_kb("a", "A", "", "root")
    kb_b = store.create_kb("b", "B", "", "root")
    a = store.create_folder(kb_a["id"], None, "A")
    child = store.create_folder(kb_a["id"], a["id"], "Child")
    b_root = store.get_root_folder(kb_b["id"])

    with pytest.raises(NotFound):
        store.create_folder(kb_a["id"], b_root["id"], "wrong-kb")
    assert store.get_folder(kb_a["id"], b_root["id"]) is None
    with pytest.raises(ValidationError):
        store.move_folder(kb_a["id"], a["id"], child["id"])


def test_document_placement_is_scoped_to_kb_and_folder_tree_counts_documents(wm_paths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    kb_a = store.create_kb("a", "A", "", "root")
    kb_b = store.create_kb("b", "B", "", "root")
    a_root = store.get_root_folder(kb_a["id"])
    b_root = store.get_root_folder(kb_b["id"])
    a_folder = store.create_folder(kb_a["id"], a_root["id"], "组件")
    a_child = store.create_folder(kb_a["id"], a_folder["id"], "后端")
    b_folder = store.create_folder(kb_b["id"], b_root["id"], "公共资料")
    doc = store.create_document("shared", "Shared", "root")

    store.attach_document_to_kb(doc["id"], kb_a["id"], "root", a_child["id"])
    store.attach_document_to_kb(doc["id"], kb_b["id"], "root", b_folder["id"])

    placements = {item["kb_id"]: item for item in store.get_document_kbs(doc["id"])}
    assert placements[kb_a["id"]]["folder_id"] == a_child["id"]
    assert placements[kb_a["id"]]["folder_path"] == "组件/后端"
    assert placements[kb_b["id"]]["folder_path"] == "公共资料"
    assert store.get_document_placement(doc["id"], kb_a["id"])["folder_id"] == a_child["id"]

    assert store.list_docs_for_kb(kb_a["id"], folder_id=a_folder["id"]) == []
    assert store.list_docs_for_kb(kb_a["id"], folder_id=a_child["id"])[0]["folder_path"] == "组件/后端"
    store.update_document_placement(doc["id"], kb_a["id"], a_folder["id"])
    assert store.list_docs_for_kb(kb_a["id"], folder_id=a_folder["id"])[0]["slug"] == "shared"

    counts = store.get_subtree_counts(kb_a["id"], a_folder["id"])
    assert counts["direct_file_count"] == 1
    assert counts["descendant_file_count"] == 1
    assert counts["descendant_folder_count"] == 1
    tree = {item["id"]: item for item in store.list_folder_tree(kb_a["id"])}
    assert tree[a_folder["id"]]["path"] == "组件"
    assert tree[a_folder["id"]]["descendant_file_count"] == 1

    with pytest.raises(NotFound):
        store.update_document_placement(doc["id"], kb_a["id"], b_folder["id"])


def test_delete_folder_subtree_removes_only_current_kb_placement_and_keeps_document(wm_paths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    kb_a = store.create_kb("a", "A", "", "root")
    kb_b = store.create_kb("b", "B", "", "root")
    a_root = store.get_root_folder(kb_a["id"])
    a_folder = store.create_folder(kb_a["id"], a_root["id"], "A")
    a_child = store.create_folder(kb_a["id"], a_folder["id"], "Child")
    b_root = store.get_root_folder(kb_b["id"])
    doc = store.create_document("shared", "Shared", "root")
    store.attach_document_to_kb(doc["id"], kb_a["id"], "root", a_child["id"])
    store.attach_document_to_kb(doc["id"], kb_b["id"], "root", b_root["id"])

    result = store.delete_folder_subtree(kb_a["id"], a_folder["id"])
    assert result["directory_count"] == 2
    assert result["file_count"] == 1
    assert store.list_docs_for_kb(kb_a["id"]) == []
    assert store.list_docs_for_kb(kb_b["id"])[0]["slug"] == "shared"
    assert store.get_document_by_slug("shared")["status"] == "active"


def test_atomic_folder_delete_detaches_docs_and_queues_current_kb_deletes(wm_paths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    kb_a = store.create_kb("a", "A", "", "root")
    kb_b = store.create_kb("b", "B", "", "root")
    root_a = store.get_root_folder(kb_a["id"])
    folder = store.create_folder(kb_a["id"], root_a["id"], "A")
    child = store.create_folder(kb_a["id"], folder["id"], "Child")
    root_b = store.get_root_folder(kb_b["id"])
    store.ensure_backend_target(kb_a["id"], "mock", "mock")

    shared = store.create_document("shared", "Shared", "root")
    store.attach_document_to_kb(shared["id"], kb_a["id"], "root", child["id"])
    store.attach_document_to_kb(shared["id"], kb_b["id"], "root", root_b["id"])
    store.create_sync_job(shared["id"], kb_a["id"], Operation.create, None)
    store.upsert_sync_state(
        shared["id"], kb_a["id"], "mock", "remote-shared", SyncStateStatus.synced
    )

    only_a = store.create_document("only-a", "Only A", "root")
    store.attach_document_to_kb(only_a["id"], kb_a["id"], "root", folder["id"])
    store.upsert_sync_state(
        only_a["id"], kb_a["id"], "mock", "remote-only-a", SyncStateStatus.synced
    )

    result = store.delete_folder_subtree_atomic(kb_a["id"], folder["id"])

    assert result["directory_count"] == 2
    assert result["file_count"] == 2
    assert store.list_docs_for_kb(kb_a["id"]) == []
    assert store.list_docs_for_kb(kb_b["id"])[0]["slug"] == "shared"
    jobs = store.list_all_jobs()
    shared_jobs = [job for job in jobs if job["doc_id"] == shared["id"]]
    assert [(job["operation"], job["status"]) for job in shared_jobs] == [
        ("create", "cancelled"),
        ("delete", "pending"),
    ]
    assert store.get_document_by_slug("shared")["status"] == "active"
    assert store.get_document_by_slug("only-a") is None
    assert store.get_document_by_slug("only-a", include_deleted=True)["status"] == "deleted"


def test_backend_folder_mapping_is_scoped_and_upserted_by_folder(wm_paths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    kb = store.create_kb("docs", "Docs", "", "root")
    root = store.get_root_folder(kb["id"])
    folder = store.create_folder(kb["id"], root["id"], "A")

    mapping = store.upsert_backend_folder_mapping(
        kb["id"], "weknora", folder["id"], "A//", "A//"
    )
    assert mapping["backend_folder_id"] == "A"
    assert mapping["path_snapshot"] == "A"
    updated = store.upsert_backend_folder_mapping(
        kb["id"], "weknora", folder["id"], "A/B", "A/B", status="synced"
    )
    assert updated["id"] == mapping["id"]
    assert store.get_backend_folder_mapping(kb["id"], "weknora", folder["id"])["status"] == "synced"
    assert store.delete_backend_folder_mappings(kb["id"], "weknora", folder["id"]) == 1
    assert store.get_backend_folder_mapping(kb["id"], "weknora", folder["id"]) is None
