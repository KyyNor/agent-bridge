from __future__ import annotations

from agent_bridge.storage.sqlite import SQLiteStore


def test_sqlite_store_uses_wal_mode_and_busy_timeout(wm_paths):
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()

    with store.connect() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]

    assert mode.lower() == "wal"
    assert busy_timeout == 5000
    assert foreign_keys == 1


def test_memory_block_crud_and_profile_binding(wm_paths):
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.upsert_project_profile(
        profile_key="dev",
        name="Dev",
        description="",
        status="active",
        created_by="root",
    )

    block = store.memory.create_memory_block(
        block_key="dev-memory",
        name="Dev Memory",
        description="Project memory",
        data_dir=str(wm_paths.data_dir / "claude-mem" / "blocks" / "dev-memory"),
        created_by="root",
    )

    assert block["block_key"] == "dev-memory"
    assert block["status"] == "active"
    assert block["last_health_json"] == "{}"

    listed = store.memory.list_memory_blocks()
    assert [item["block_key"] for item in listed] == ["dev-memory"]
    assert listed[0]["bound_profile_count"] == 0

    store.memory.set_profile_memory_binding("dev", "dev-memory", enabled=True)
    binding = store.memory.get_profile_memory_binding("dev")
    assert binding == {
        "profile_key": "dev",
        "block_key": "dev-memory",
        "enabled": 1,
    }

    listed = store.memory.list_memory_blocks()
    assert listed[0]["bound_profile_count"] == 1

    store.memory.set_memory_block_status("dev-memory", "disabled")
    updated = store.memory.get_memory_block("dev-memory")
    assert updated is not None
    assert updated["status"] == "disabled"


def test_memory_binding_survives_as_null_when_block_deleted(wm_paths):
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.upsert_project_profile(
        profile_key="dev",
        name="Dev",
        description="",
        status="active",
        created_by="root",
    )
    store.memory.create_memory_block(
        block_key="dev-memory",
        name="Dev Memory",
        description="",
        data_dir=str(wm_paths.data_dir / "claude-mem" / "blocks" / "dev-memory"),
        created_by="root",
    )
    store.memory.set_profile_memory_binding("dev", "dev-memory", enabled=True)

    store.memory.delete_memory_block("dev-memory")
    binding = store.memory.get_profile_memory_binding("dev")

    assert binding == {"profile_key": "dev", "block_key": None, "enabled": 1}
