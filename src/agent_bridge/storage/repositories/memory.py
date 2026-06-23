"""SQLite memory repository."""

from __future__ import annotations

import json
from typing import Any

from agent_bridge.storage.types import row_to_dict


class MemoryRepository:
    def __init__(self, db_path, connect):
        self._db_path = db_path
        self._connect = connect

    def create_memory_block(
        self,
        *,
        block_key: str,
        name: str,
        description: str,
        data_dir: str,
        created_by: str,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_blocks (block_key, name, description, data_dir, created_by)
                VALUES (?, ?, ?, ?, ?)
                """,
                (block_key, name, description, data_dir, created_by),
            )
            row = conn.execute("SELECT * FROM memory_blocks WHERE block_key = ?", (block_key,)).fetchone()
            block = row_to_dict(row)
            if block is None:
                raise KeyError(f"memory block not found: {block_key}")
            return block

    def list_memory_blocks(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  block.*,
                  COUNT(binding.profile_key) AS bound_profile_count
                FROM memory_blocks block
                LEFT JOIN profile_memory_bindings binding ON binding.block_key = block.block_key
                GROUP BY block.block_key
                ORDER BY block.block_key
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def get_memory_block(self, block_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM memory_blocks WHERE block_key = ?", (block_key,)).fetchone()
            return row_to_dict(row)

    def set_memory_block_status(self, block_key: str, status: str) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE memory_blocks
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE block_key = ?
                """,
                (status, block_key),
            )
            row = conn.execute("SELECT * FROM memory_blocks WHERE block_key = ?", (block_key,)).fetchone()
            block = row_to_dict(row)
            if block is None:
                raise KeyError(f"memory block not found: {block_key}")
            return block

    def update_memory_block_health(self, block_key: str, health: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE memory_blocks
                SET last_health_json = ?, updated_at = CURRENT_TIMESTAMP
                WHERE block_key = ?
                """,
                (json.dumps(health, ensure_ascii=False, default=str), block_key),
            )

    def delete_memory_block(self, block_key: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM memory_blocks WHERE block_key = ?", (block_key,))

    def get_profile_memory_binding(self, profile_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT profile_key, block_key, enabled
                FROM profile_memory_bindings
                WHERE profile_key = ?
                """,
                (profile_key,),
            ).fetchone()
            return row_to_dict(row)

    def set_profile_memory_binding(self, profile_key: str, block_key: str | None, *, enabled: bool) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO profile_memory_bindings (profile_key, block_key, enabled)
                VALUES (?, ?, ?)
                ON CONFLICT(profile_key) DO UPDATE SET
                  block_key = excluded.block_key,
                  enabled = excluded.enabled,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (profile_key, block_key, int(enabled)),
            )
            row = conn.execute(
                """
                SELECT profile_key, block_key, enabled
                FROM profile_memory_bindings
                WHERE profile_key = ?
                """,
                (profile_key,),
            ).fetchone()
            binding = row_to_dict(row)
            if binding is None:
                raise KeyError(f"profile memory binding not found: {profile_key}")
            return binding
