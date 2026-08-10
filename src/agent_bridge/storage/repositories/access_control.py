"""小组成员关系持久化。"""

from __future__ import annotations

from typing import Any

from agent_bridge.storage.types import row_to_dict


class AccessControlRepository:
    def __init__(self, db_path, connect) -> None:
        self._db_path = db_path
        self._connect = connect

    def upsert_group(
        self,
        *,
        group_key: str,
        name: str,
        description: str,
        actor: str,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO access_groups (group_key, name, description, created_by)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(group_key) DO UPDATE SET
                  name = excluded.name,
                  description = excluded.description,
                  status = 'active',
                  updated_at = CURRENT_TIMESTAMP
                """,
                (group_key, name, description, actor),
            )
            row = conn.execute(
                "SELECT * FROM access_groups WHERE group_key = ?", (group_key,)
            ).fetchone()
            group = row_to_dict(row)
            if group is None:
                raise KeyError(f"group not found: {group_key}")
            return group

    def get_group(self, group_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM access_groups WHERE group_key = ? AND status = 'active'",
                (group_key,),
            ).fetchone()
            return row_to_dict(row)

    def list_groups(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM access_groups WHERE status = 'active' ORDER BY group_key"
            ).fetchall()
            return [dict(row) for row in rows]

    def set_membership(self, *, user_id: str, group_key: str, actor: str) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_group_memberships (user_id, group_key, updated_by)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                  group_key = excluded.group_key,
                  updated_by = excluded.updated_by,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, group_key, actor),
            )
            row = conn.execute(
                """
                SELECT membership.*, groups.name AS group_name
                FROM user_group_memberships membership
                JOIN access_groups groups ON groups.group_key = membership.group_key
                WHERE membership.user_id = ?
                """,
                (user_id,),
            ).fetchone()
            membership = row_to_dict(row)
            if membership is None:
                raise KeyError(f"membership not found: {user_id}")
            return membership

    def get_membership(self, user_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT membership.*, groups.name AS group_name
                FROM user_group_memberships membership
                JOIN access_groups groups ON groups.group_key = membership.group_key
                WHERE membership.user_id = ? AND groups.status = 'active'
                """,
                (user_id,),
            ).fetchone()
            return row_to_dict(row)

    def list_memberships(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT membership.*, groups.name AS group_name
                FROM user_group_memberships membership
                JOIN access_groups groups ON groups.group_key = membership.group_key
                WHERE groups.status = 'active'
                ORDER BY membership.group_key, membership.user_id
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def delete_membership(self, user_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM user_group_memberships WHERE user_id = ?", (user_id,)
            )
            return cursor.rowcount > 0
