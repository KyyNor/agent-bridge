"""Regression test for the missing default_backend_slug / default_agent_id columns.

A previous version created knowledge_bases without these columns, but the
migrate_phase2() hook never added them for existing databases, so
PUT /kbs/{slug}/defaults raised ``sqlite3.OperationalError: no such column``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent_bridge.storage.sqlite import SQLiteStore


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_migrate_phase2_adds_kb_defaults_columns(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "test.db")
    store.init_schema()

    # Simulate an upgrade from a DB created before these columns existed:
    # drop them, recreating the exact failing condition.
    with store.connect() as conn:
        assert "default_backend_slug" in _columns(conn, "knowledge_bases")
        assert "sync_on_upload" in _columns(conn, "knowledge_bases")
        assert "last_error" in _columns(conn, "backend_targets")
        kb = store.create_kb(slug="test-kb", name="Test", description="", created_by="tester")
        conn.execute("ALTER TABLE knowledge_bases DROP COLUMN default_backend_slug")
        conn.execute("ALTER TABLE knowledge_bases DROP COLUMN default_agent_id")
        conn.execute("ALTER TABLE knowledge_bases DROP COLUMN sync_on_upload")
        conn.execute("ALTER TABLE backend_targets DROP COLUMN last_error")
        assert "default_backend_slug" not in _columns(conn, "knowledge_bases")
        assert "sync_on_upload" not in _columns(conn, "knowledge_bases")
        assert "last_error" not in _columns(conn, "backend_targets")

    # Without the migration, update_kb_defaults reproduces the original crash.
    with pytest.raises(sqlite3.OperationalError):
        store.update_kb_defaults(kb["id"], "some-backend", "agent-1")

    # The startup migration repairs the schema.
    store.migrate_phase2()
    with store.connect() as conn:
        cols = _columns(conn, "knowledge_bases")
        assert "default_backend_slug" in cols
        assert "default_agent_id" in cols
        assert "sync_on_upload" in cols
        assert "last_error" in _columns(conn, "backend_targets")

    # The previously-failing write now succeeds and persists.
    store.update_kb_defaults(kb["id"], "some-backend", "agent-1")
    updated = store.get_kb_by_slug("test-kb")
    assert updated is not None
    assert updated["default_backend_slug"] == "some-backend"
    assert updated["default_agent_id"] == "agent-1"
    assert updated["sync_on_upload"] == 0

    store.update_kb_sync_policy(kb["id"], True)
    assert store.get_kb_by_slug("test-kb")["sync_on_upload"] == 1

    # Idempotent: running the migration again is a no-op.
    store.migrate_phase2()


def test_migrate_phase2_adds_documents_source_columns(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "test.db")
    store.init_schema()

    # Simulate an upgrade from a DB created before these columns existed.
    with store.connect() as conn:
        assert "source_type" in _columns(conn, "documents")
        conn.execute("ALTER TABLE documents DROP COLUMN source_type")
        conn.execute("ALTER TABLE documents DROP COLUMN source_repo_key")
        assert "source_type" not in _columns(conn, "documents")

    # The startup migration repairs the schema.
    store.migrate_phase2()
    with store.connect() as conn:
        cols = _columns(conn, "documents")
        assert "source_type" in cols
        assert "source_repo_key" in cols
        # 默认值正确:不显式指定时为 manual / 空
        conn.execute(
            "INSERT INTO documents (slug, title, owner_user) VALUES ('t', 'T', 'root')"
        )
        row = conn.execute(
            "SELECT source_type, source_repo_key FROM documents WHERE slug='t'"
        ).fetchone()
        assert row[0] == "manual"
        assert row[1] == ""
