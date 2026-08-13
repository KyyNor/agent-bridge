"""小组成员关系持久化。"""

from __future__ import annotations

from typing import Any

from agent_bridge.storage.types import row_to_dict


class AccessControlRepository:
    def __init__(self, db_path, connect) -> None:
        self._db_path = db_path
        self._connect = connect

    def create_user(self, *, user_id: str, actor: str) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO access_users (user_id, created_by) VALUES (?, ?)",
                (user_id, actor),
            )
            row = conn.execute(
                "SELECT * FROM access_users WHERE user_id = ?", (user_id,)
            ).fetchone()
            user = row_to_dict(row)
            if user is None:
                raise KeyError(f"user not found: {user_id}")
            return user

    def ensure_user(self, *, user_id: str, actor: str) -> None:
        """为历史脚本和旧接口保留幂等登记；管理界面应显式创建用户。"""
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO access_users (user_id, created_by) VALUES (?, ?)",
                (user_id, actor),
            )

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM access_users WHERE user_id = ? AND status = 'active'",
                (user_id,),
            ).fetchone()
            return row_to_dict(row)

    def list_users(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT users.*, membership.group_key, groups.name AS group_name
                FROM access_users users
                LEFT JOIN user_group_memberships membership ON membership.user_id = users.user_id
                LEFT JOIN access_groups groups ON groups.group_key = membership.group_key
                WHERE users.status = 'active'
                ORDER BY users.user_id
                """
            ).fetchall()
            return [dict(row) for row in rows]

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
                """
                SELECT groups.*, COUNT(membership.user_id) AS member_count
                FROM access_groups groups
                LEFT JOIN user_group_memberships membership ON membership.group_key = groups.group_key
                WHERE groups.status = 'active'
                GROUP BY groups.group_key
                ORDER BY groups.group_key
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def group_member_count(self, group_key: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM user_group_memberships WHERE group_key = ?",
                (group_key,),
            ).fetchone()
            return int(row["count"] if row else 0)

    def delete_group(self, group_key: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM access_groups WHERE group_key = ?", (group_key,)
            )
            return cursor.rowcount > 0

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

    def get_admin_access_config(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM admin_access_config WHERE id = 1"
            ).fetchone()
            return row_to_dict(row)

    def initialize_admin_access(
        self,
        *,
        password_hash: str,
        session_secret: str,
        actor: str,
    ) -> bool:
        """仅在尚未设置时初始化；并发请求中只有一个调用者会成功。"""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO admin_access_config (
                  id, password_hash, session_secret, updated_by
                ) VALUES (1, ?, ?, ?)
                """,
                (password_hash, session_secret, actor),
            )
            return cursor.rowcount > 0

    def update_admin_access(
        self,
        *,
        password_hash: str,
        session_secret: str,
        actor: str,
    ) -> None:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE admin_access_config
                SET password_hash = ?, session_secret = ?, updated_by = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = 1
                """,
                (password_hash, session_secret, actor),
            )
            if cursor.rowcount != 1:
                raise KeyError("admin access config not found")
