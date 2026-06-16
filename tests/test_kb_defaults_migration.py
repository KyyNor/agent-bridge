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
        conn.execute("ALTER TABLE knowledge_bases DROP COLUMN default_backend_slug")
        conn.execute("ALTER TABLE knowledge_bases DROP COLUMN default_agent_id")
        assert "default_backend_slug" not in _columns(conn, "knowledge_bases")

    kb = store.create_kb(slug="test-kb", name="Test", description="", created_by="tester")

    # Without the migration, update_kb_defaults reproduces the original crash.
    with pytest.raises(sqlite3.OperationalError):
        store.update_kb_defaults(kb["id"], "some-backend", "agent-1")

    # The startup migration repairs the schema.
    store.migrate_phase2()
    with store.connect() as conn:
        cols = _columns(conn, "knowledge_bases")
        assert "default_backend_slug" in cols
        assert "default_agent_id" in cols

    # The previously-failing write now succeeds and persists.
    store.update_kb_defaults(kb["id"], "some-backend", "agent-1")
    updated = store.get_kb_by_slug("test-kb")
    assert updated is not None
    assert updated["default_backend_slug"] == "some-backend"
    assert updated["default_agent_id"] == "agent-1"

    # Idempotent: running the migration again is a no-op.
    store.migrate_phase2()
