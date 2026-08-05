from __future__ import annotations

import sqlite3

from agent_bridge.core.config import migrate_legacy_database_filename
from agent_bridge.storage.sqlite import SQLiteStore


def test_legacy_wiki_db_is_copied_to_agent_bridge_db(wm_paths) -> None:
    wm_paths.data_dir.mkdir(parents=True)
    legacy_path = wm_paths.data_dir / "wiki.db"
    with sqlite3.connect(legacy_path) as conn:
        conn.execute("CREATE TABLE marker (value TEXT NOT NULL)")
        conn.execute("INSERT INTO marker (value) VALUES ('kept')")

    assert migrate_legacy_database_filename(wm_paths) is True
    assert migrate_legacy_database_filename(wm_paths) is False
    with sqlite3.connect(wm_paths.db_path) as conn:
        assert conn.execute("SELECT value FROM marker").fetchone()[0] == "kept"


def test_runtime_logs_are_migrated_to_dedicated_database(wm_paths) -> None:
    legacy_store = SQLiteStore(wm_paths.db_path)
    legacy_store.init_schema()
    legacy_store.create_tool_call_log(
        log_id="call_1",
        actor="root",
        profile_key=None,
        entrypoint="test",
        status="success",
    )
    legacy_store.agent_runs.create(run_key="run_1", agent_name="test", prompt="hello")

    store = SQLiteStore(wm_paths.db_path, wm_paths.log_db_path)
    store.init_schema()

    assert store.get_tool_call_log("call_1") is not None
    assert store.agent_runs.get("run_1") is not None
    with sqlite3.connect(wm_paths.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tool_call_logs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0] == 0
