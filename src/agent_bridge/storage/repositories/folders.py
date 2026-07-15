"""SQLite repository for knowledge-base folder trees."""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_bridge.core.domain import ConflictError, NotFound, ValidationError
from agent_bridge.storage.types import row_to_dict


_FOLDER_TREE_CTE = """
WITH RECURSIVE folder_tree AS (
  SELECT
    id,
    kb_id,
    parent_id,
    name,
    is_root,
    created_at,
    updated_at,
    '' AS path
  FROM knowledge_folders
  WHERE kb_id = ? AND parent_id IS NULL AND is_root = 1
  UNION ALL
  SELECT
    child.id,
    child.kb_id,
    child.parent_id,
    child.name,
    child.is_root,
    child.created_at,
    child.updated_at,
    CASE WHEN folder_tree.path = '' THEN child.name
         ELSE folder_tree.path || '/' || child.name END AS path
  FROM knowledge_folders child
  JOIN folder_tree ON folder_tree.id = child.parent_id
                  AND folder_tree.kb_id = child.kb_id
)
"""


class FolderRepository:
    def __init__(self, db_path, connect):
        self._db_path = db_path
        self._connect = connect

    def ensure_root_folder(
        self,
        kb_id: int,
        conn: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        if conn is not None:
            return self._ensure_root_folder(conn, kb_id)
        with self._connect() as own_conn:
            return self._ensure_root_folder(own_conn, kb_id)

    def _ensure_root_folder(self, conn: sqlite3.Connection, kb_id: int) -> dict[str, Any]:
        row = conn.execute(
            "SELECT id, name FROM knowledge_bases WHERE id = ?",
            (kb_id,),
        ).fetchone()
        if row is None:
            raise NotFound("knowledge base not found")

        existing = conn.execute(
            """
            SELECT *
            FROM knowledge_folders
            WHERE kb_id = ? AND parent_id IS NULL AND is_root = 1
            """,
            (kb_id,),
        ).fetchone()
        if existing is None:
            cursor = conn.execute(
                """
                INSERT INTO knowledge_folders (kb_id, parent_id, name, is_root)
                VALUES (?, NULL, ?, 1)
                """,
                (kb_id, row["name"]),
            )
            folder_id = int(cursor.lastrowid)
        else:
            folder_id = int(existing["id"])
        return self._get_folder_with_conn(conn, kb_id, folder_id)  # type: ignore[return-value]

    def get_root_folder(self, kb_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id
                FROM knowledge_folders
                WHERE kb_id = ? AND parent_id IS NULL AND is_root = 1
                """,
                (kb_id,),
            ).fetchone()
            if row is None:
                return None
            return self._get_folder_with_conn(conn, kb_id, int(row["id"]))

    def get_folder(self, kb_id: int, folder_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            return self._get_folder_with_conn(conn, kb_id, folder_id)

    def _get_folder_with_conn(
        self,
        conn: sqlite3.Connection,
        kb_id: int,
        folder_id: int,
    ) -> dict[str, Any] | None:
        row = conn.execute(
            _FOLDER_TREE_CTE
            + """
            SELECT *
            FROM folder_tree
            WHERE kb_id = ? AND id = ?
            """,
            (kb_id, kb_id, folder_id),
        ).fetchone()
        return row_to_dict(row)

    def _require_folder(
        self,
        conn: sqlite3.Connection,
        kb_id: int,
        folder_id: int,
    ) -> dict[str, Any]:
        folder = self._get_folder_with_conn(conn, kb_id, folder_id)
        if folder is None:
            raise NotFound("folder not found")
        return folder

    def _resolve_parent(
        self,
        conn: sqlite3.Connection,
        kb_id: int,
        parent_id: int | None,
    ) -> dict[str, Any]:
        if parent_id is None:
            root = self._ensure_root_folder(conn, kb_id)
            return root
        return self._require_folder(conn, kb_id, parent_id)

    @staticmethod
    def _normalise_name(name: str) -> str:
        if not isinstance(name, str):
            raise ValidationError("folder name must be a string")
        if any(ord(char) < 32 or ord(char) == 127 for char in name):
            raise ValidationError("folder name contains a control character")
        value = name.strip()
        if not value or value in {".", ".."}:
            raise ValidationError("folder name is invalid")
        if "/" in value or "\\" in value:
            raise ValidationError("folder name must be a single path segment")
        return value

    @staticmethod
    def _normalise_remote_path(path: str) -> str:
        if not isinstance(path, str):
            raise ValidationError("backend folder path must be a string")
        raw = path.replace("\\", "/")
        parts: list[str] = []
        for part in raw.split("/"):
            if not part or part == ".":
                continue
            if part == ".." or any(ord(char) < 32 or ord(char) == 127 for char in part):
                raise ValidationError("backend folder path is invalid")
            parts.append(part)
        return "/".join(parts)

    def create_folder(self, kb_id: int, parent_id: int | None, name: str) -> dict[str, Any]:
        folder_name = self._normalise_name(name)
        with self._connect() as conn:
            parent = self._resolve_parent(conn, kb_id, parent_id)
            existing = conn.execute(
                """
                SELECT id FROM knowledge_folders
                WHERE kb_id = ? AND parent_id = ? AND name = ?
                """,
                (kb_id, parent["id"], folder_name),
            ).fetchone()
            if existing is not None:
                raise ConflictError("folder name already exists")
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO knowledge_folders (kb_id, parent_id, name, is_root)
                    VALUES (?, ?, ?, 0)
                    """,
                    (kb_id, parent["id"], folder_name),
                )
            except sqlite3.IntegrityError as exc:
                raise ConflictError("folder name already exists") from exc
            return self._get_folder_with_conn(conn, kb_id, int(cursor.lastrowid))  # type: ignore[return-value]

    def rename_folder(self, kb_id: int, folder_id: int, name: str) -> dict[str, Any]:
        folder_name = self._normalise_name(name)
        with self._connect() as conn:
            folder = self._require_folder(conn, kb_id, folder_id)
            if folder["is_root"]:
                raise ValidationError("root folder cannot be renamed")
            existing = conn.execute(
                """
                SELECT id FROM knowledge_folders
                WHERE kb_id = ? AND parent_id = ? AND name = ? AND id != ?
                """,
                (kb_id, folder["parent_id"], folder_name, folder_id),
            ).fetchone()
            if existing is not None:
                raise ConflictError("folder name already exists")
            conn.execute(
                """
                UPDATE knowledge_folders
                SET name = ?, updated_at = CURRENT_TIMESTAMP
                WHERE kb_id = ? AND id = ?
                """,
                (folder_name, kb_id, folder_id),
            )
            return self._get_folder_with_conn(conn, kb_id, folder_id)  # type: ignore[return-value]

    def move_folder(self, kb_id: int, folder_id: int, parent_id: int | None) -> dict[str, Any]:
        with self._connect() as conn:
            folder = self._require_folder(conn, kb_id, folder_id)
            if folder["is_root"]:
                raise ValidationError("root folder cannot be moved")
            parent = self._resolve_parent(conn, kb_id, parent_id)
            subtree_ids = self._get_subtree_ids_with_conn(conn, kb_id, folder_id)
            if int(parent["id"]) in subtree_ids:
                raise ValidationError("folder cannot be moved into itself or its descendant")
            if int(parent["id"]) == int(folder["parent_id"]):
                return folder
            existing = conn.execute(
                """
                SELECT id FROM knowledge_folders
                WHERE kb_id = ? AND parent_id = ? AND name = ? AND id != ?
                """,
                (kb_id, parent["id"], folder["name"], folder_id),
            ).fetchone()
            if existing is not None:
                raise ConflictError("folder name already exists")
            conn.execute(
                """
                UPDATE knowledge_folders
                SET parent_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE kb_id = ? AND id = ?
                """,
                (parent["id"], kb_id, folder_id),
            )
            return self._get_folder_with_conn(conn, kb_id, folder_id)  # type: ignore[return-value]

    def update_folder(
        self,
        kb_id: int,
        folder_id: int,
        *,
        name: str | None = None,
        parent_id: int | None = None,
        parent_provided: bool = False,
    ) -> dict[str, Any]:
        """Atomically rename and/or move a folder within one KB."""
        folder_name = self._normalise_name(name) if name is not None else None
        if folder_name is None and not parent_provided:
            raise ValidationError("at least one folder field must be provided")

        with self._connect() as conn:
            folder = self._require_folder(conn, kb_id, folder_id)
            if folder["is_root"]:
                if folder_name is not None:
                    raise ValidationError("root folder cannot be renamed")
                if parent_provided:
                    raise ValidationError("root folder cannot be moved")

            new_parent_id = folder["parent_id"]
            if parent_provided:
                parent = self._resolve_parent(conn, kb_id, parent_id)
                subtree_ids = self._get_subtree_ids_with_conn(conn, kb_id, folder_id)
                if int(parent["id"]) in subtree_ids:
                    raise ValidationError("folder cannot be moved into itself or its descendant")
                new_parent_id = parent["id"]

            new_name = folder_name or folder["name"]
            existing = conn.execute(
                """
                SELECT id FROM knowledge_folders
                WHERE kb_id = ? AND parent_id IS ? AND name = ? AND id != ?
                """,
                (kb_id, new_parent_id, new_name, folder_id),
            ).fetchone()
            if existing is not None:
                raise ConflictError("folder name already exists")

            conn.execute(
                """
                UPDATE knowledge_folders
                SET parent_id = ?, name = ?, updated_at = CURRENT_TIMESTAMP
                WHERE kb_id = ? AND id = ?
                """,
                (new_parent_id, new_name, kb_id, folder_id),
            )
            return self._get_folder_with_conn(conn, kb_id, folder_id)  # type: ignore[return-value]

    def list_folder_tree(self, kb_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                _FOLDER_TREE_CTE
                + "SELECT * FROM folder_tree ORDER BY path, id",
                (kb_id,),
            ).fetchall()
            tree = [dict(row) for row in rows]
            for item in tree:
                counts = self._get_subtree_counts_with_conn(conn, kb_id, int(item["id"]))
                item.update(
                    direct_file_count=counts["direct_file_count"],
                    descendant_file_count=counts["descendant_file_count"],
                    descendant_folder_count=counts["descendant_folder_count"],
                )
            return tree

    def get_subtree_ids(self, kb_id: int, folder_id: int) -> list[int]:
        with self._connect() as conn:
            return sorted(self._get_subtree_ids_with_conn(conn, kb_id, folder_id))

    def _get_subtree_ids_with_conn(
        self,
        conn: sqlite3.Connection,
        kb_id: int,
        folder_id: int,
    ) -> set[int]:
        self._require_folder(conn, kb_id, folder_id)
        rows = conn.execute(
            """
            WITH RECURSIVE subtree(id) AS (
              SELECT id FROM knowledge_folders WHERE kb_id = ? AND id = ?
              UNION ALL
              SELECT child.id
              FROM knowledge_folders child
              JOIN subtree parent ON parent.id = child.parent_id
              WHERE child.kb_id = ?
            )
            SELECT id FROM subtree
            """,
            (kb_id, folder_id, kb_id),
        ).fetchall()
        return {int(row["id"]) for row in rows}

    def get_subtree_counts(self, kb_id: int, folder_id: int) -> dict[str, int]:
        with self._connect() as conn:
            return self._get_subtree_counts_with_conn(conn, kb_id, folder_id)

    def _get_subtree_counts_with_conn(
        self,
        conn: sqlite3.Connection,
        kb_id: int,
        folder_id: int,
    ) -> dict[str, int]:
        ids = self._get_subtree_ids_with_conn(conn, kb_id, folder_id)
        placeholders = ", ".join("?" for _ in ids)
        values = [kb_id, *sorted(ids)]
        descendant = conn.execute(
            f"""
            SELECT COUNT(DISTINCT dk.doc_id) AS count
            FROM document_kbs dk
            JOIN documents d ON d.id = dk.doc_id
            WHERE dk.kb_id = ? AND dk.status = 'active'
              AND d.status != 'deleted'
              AND dk.folder_id IN ({placeholders})
            """,
            values,
        ).fetchone()["count"]
        direct = conn.execute(
            """
            SELECT COUNT(DISTINCT dk.doc_id) AS count
            FROM document_kbs dk
            JOIN documents d ON d.id = dk.doc_id
            WHERE dk.kb_id = ? AND dk.status = 'active'
              AND d.status != 'deleted' AND dk.folder_id = ?
            """,
            (kb_id, folder_id),
        ).fetchone()["count"]
        result = {
            "directory_count": len(ids),
            "folder_count": len(ids),
            "file_count": int(descendant),
            "direct_file_count": int(direct),
            "descendant_file_count": int(descendant),
            "descendant_folder_count": max(len(ids) - 1, 0),
        }
        return result

    def delete_folder_subtree(self, kb_id: int, folder_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            folder = self._require_folder(conn, kb_id, folder_id)
            if folder["is_root"]:
                raise ValidationError("root folder cannot be deleted")
            ids = self._get_subtree_ids_with_conn(conn, kb_id, folder_id)
            counts = self._get_subtree_counts_with_conn(conn, kb_id, folder_id)
            placeholders = ", ".join("?" for _ in ids)
            sorted_ids = sorted(ids)
            conn.execute(
                f"DELETE FROM document_kbs WHERE kb_id = ? AND folder_id IN ({placeholders})",
                [kb_id, *sorted_ids],
            )
            conn.execute(
                f"DELETE FROM knowledge_folders WHERE kb_id = ? AND id IN ({placeholders})",
                [kb_id, *sorted_ids],
            )
            return {**counts, "directory_ids": sorted_ids}

    def upsert_backend_folder_mapping(
        self,
        kb_id: int,
        backend_slug: str,
        folder_id: int,
        backend_folder_id: str,
        path_snapshot: str,
        status: str = "active",
        error: str | None = None,
    ) -> dict[str, Any]:
        backend_path = self._normalise_remote_path(backend_folder_id)
        snapshot = self._normalise_remote_path(path_snapshot)
        with self._connect() as conn:
            self._require_folder(conn, kb_id, folder_id)
            conn.execute(
                """
                INSERT INTO backend_folder_mappings (
                  kb_id, backend_slug, folder_id, backend_folder_id,
                  path_snapshot, status, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(kb_id, backend_slug, folder_id) DO UPDATE SET
                  backend_folder_id = excluded.backend_folder_id,
                  path_snapshot = excluded.path_snapshot,
                  status = excluded.status,
                  error = excluded.error,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (kb_id, backend_slug, folder_id, backend_path, snapshot, status, error),
            )
            row = conn.execute(
                """
                SELECT * FROM backend_folder_mappings
                WHERE kb_id = ? AND backend_slug = ? AND folder_id = ?
                """,
                (kb_id, backend_slug, folder_id),
            ).fetchone()
            mapping = row_to_dict(row)
            if mapping is None:
                raise KeyError("backend folder mapping not found")
            return mapping

    def get_backend_folder_mapping(
        self,
        kb_id: int,
        backend_slug: str,
        folder_id: int,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT mapping.*
                FROM backend_folder_mappings mapping
                JOIN knowledge_folders folder
                  ON folder.id = mapping.folder_id AND folder.kb_id = mapping.kb_id
                WHERE mapping.kb_id = ? AND mapping.backend_slug = ? AND mapping.folder_id = ?
                """,
                (kb_id, backend_slug, folder_id),
            ).fetchone()
            return row_to_dict(row)

    def delete_backend_folder_mappings(
        self,
        kb_id: int,
        backend_slug: str | None = None,
        folder_id: int | None = None,
    ) -> int:
        with self._connect() as conn:
            if folder_id is not None:
                self._require_folder(conn, kb_id, folder_id)
            clauses = ["kb_id = ?"]
            params: list[Any] = [kb_id]
            if backend_slug is not None:
                clauses.append("backend_slug = ?")
                params.append(backend_slug)
            if folder_id is not None:
                clauses.append("folder_id = ?")
                params.append(folder_id)
            cursor = conn.execute(
                f"DELETE FROM backend_folder_mappings WHERE {' AND '.join(clauses)}",
                params,
            )
            return cursor.rowcount
