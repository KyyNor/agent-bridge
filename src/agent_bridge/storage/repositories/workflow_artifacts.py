"""工作流产物存储、复用校验与搜索的持久化。"""

from __future__ import annotations

from typing import Any

from .workflow_artifact_search import (
    build_artifact_fts_query,
    upsert_workflow_artifact_search_content,
)
from .workflow_common import _artifact_id, _content_hash, _json_dumps, _row_payload


class WorkflowArtifactsRepositoryMixin:
    @staticmethod
    def _artifact_search_source(query: str | None) -> tuple[str, str]:
        has_query = build_artifact_fts_query(query) is not None
        from_sql = "workflow_artifacts a"
        if has_query:
            from_sql += " JOIN workflow_artifacts_fts ON workflow_artifacts_fts.rowid = a.id"
        order_by = (
            "bm25(workflow_artifacts_fts) ASC, a.updated_at DESC, a.id DESC"
            if has_query
            else "a.updated_at DESC, a.id DESC"
        )
        return from_sql, order_by

    def upsert_workflow_artifact(
        self,
        *,
        workflow_key: str,
        profile_key: str,
        run_id: str,
        task_key: str | None,
        title: str,
        path: str,
        tags: list[str],
        format: str,
        summary: str,
        content: str,
        metadata: dict[str, Any],
        task_version: str = "",
        producer_node_id: str | None = None,
        producer_node_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        content_hash = _content_hash(content)
        with self._connect() as conn:
            run_row = conn.execute(
                "SELECT status FROM workflow_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            run_status = str(run_row["status"]) if run_row is not None else "completed"
            is_current = 1 if run_status == "completed" else 0
            if is_current:
                conn.execute(
                    """
                    UPDATE workflow_artifacts
                    SET is_current = 0
                    WHERE workflow_key = ?
                      AND task_key IS ?
                      AND run_id <> ?
                    """,
                    (workflow_key, task_key, run_id),
                )
            existing = conn.execute(
                """
                SELECT artifact_id FROM workflow_artifacts
                WHERE workflow_key = ? AND task_key IS ? AND task_version = ? AND run_id = ? AND path = ?
                """,
                (workflow_key, task_key, task_version, run_id, path),
            ).fetchone()
            artifact_id = existing["artifact_id"] if existing else _artifact_id()
            conn.execute(
                """
                INSERT INTO workflow_artifacts (
                  artifact_id, workflow_key, profile_key, run_id, task_key, task_version,
                  is_current, reuse_allowed, invalid_reason, producer_node_id,
                  producer_node_fingerprint, title, path,
                  tags_json, format, summary, content, content_hash, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workflow_key, task_key, task_version, run_id, path) DO UPDATE SET
                  profile_key = excluded.profile_key,
                  run_id = excluded.run_id,
                  task_key = excluded.task_key,
                  task_version = excluded.task_version,
                  is_current = excluded.is_current,
                  reuse_allowed = excluded.reuse_allowed,
                  invalid_reason = excluded.invalid_reason,
                  producer_node_id = excluded.producer_node_id,
                  producer_node_fingerprint = excluded.producer_node_fingerprint,
                  title = excluded.title,
                  tags_json = excluded.tags_json,
                  format = excluded.format,
                  summary = excluded.summary,
                  content = excluded.content,
                  content_hash = excluded.content_hash,
                  metadata_json = excluded.metadata_json,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (
                    artifact_id,
                    workflow_key,
                    profile_key,
                    run_id,
                    task_key,
                    task_version,
                    is_current,
                    int(metadata.get("reuse_allowed", True)),
                    metadata.get("invalid_reason"),
                    producer_node_id,
                    producer_node_fingerprint,
                    title,
                    path,
                    _json_dumps(tags),
                    format,
                    summary,
                    content,
                    content_hash,
                    _json_dumps(metadata),
                ),
            )
            result = _row_payload(
                conn.execute(
                    "SELECT * FROM workflow_artifacts WHERE artifact_id = ?",
                    (artifact_id,),
                ).fetchone()
            )
            if result is None:
                raise KeyError(f"workflow artifact not found: {artifact_id}")
            upsert_workflow_artifact_search_content(conn, result)
            return result

    def list_artifacts_for_run(self, run_id: str, *, include_reused: bool = True) -> list[dict[str, Any]]:
        """Return physical and reused artifacts visible in one run context."""
        with self._connect() as conn:
            current = conn.execute(
                "SELECT workflow_key, profile_key, task_key, task_version FROM workflow_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if current is None:
                return []
            rows = conn.execute(
                """
                SELECT a.*, NULL AS linked_node_id, NULL AS source_run_id, NULL AS source_node_id,
                       0 AS reused
                FROM workflow_artifacts a
                WHERE a.run_id = ?
                ORDER BY a.updated_at DESC, a.id DESC
                """,
                (run_id,),
            ).fetchall()
            if include_reused:
                rows += conn.execute(
                    """
                    SELECT a.*, rra.node_id AS linked_node_id, rra.source_run_id, rra.source_node_id,
                           1 AS reused
                    FROM workflow_run_artifacts rra
                    JOIN workflow_artifacts a ON a.artifact_id = rra.artifact_id
                    WHERE rra.run_id = ? AND a.run_id <> ?
                    ORDER BY a.updated_at DESC, a.id DESC
                    """,
                    (run_id, run_id),
                ).fetchall()

        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            item = _row_payload(row)
            if item is None or item["artifact_id"] in seen:
                continue
            seen.add(item["artifact_id"])
            item["reused"] = bool(row["reused"])
            item["source_run_id"] = row["source_run_id"]
            item["source_node_id"] = row["source_node_id"] or row["linked_node_id"]
            item["reusable"], item["reuse_validation_reason"] = self._artifact_reuse_validation(
                item,
                current_workflow_key=current["workflow_key"],
                current_profile_key=current["profile_key"],
                current_task_key=current["task_key"],
                current_task_version=current["task_version"],
            )
            result.append(item)
        return result

    @staticmethod
    def _artifact_reuse_validation(
        artifact: dict[str, Any],
        *,
        current_workflow_key: str,
        current_profile_key: str,
        current_task_key: str | None,
        current_task_version: str,
    ) -> tuple[bool, str | None]:
        if artifact.get("workflow_key") != current_workflow_key or artifact.get("profile_key") != current_profile_key:
            return False, "artifact_scope_mismatch"
        if artifact.get("task_key") != current_task_key or str(artifact.get("task_version") or "") != str(current_task_version or ""):
            return False, "artifact_scope_mismatch"
        if artifact.get("reuse_allowed") is False:
            return False, "reuse_disabled"
        if artifact.get("invalid_reason"):
            return False, str(artifact["invalid_reason"])
        if _content_hash(str(artifact.get("content") or "")) != str(artifact.get("content_hash") or ""):
            return False, "content_hash_mismatch"
        return True, None

    def get_workflow_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            return _row_payload(
                conn.execute(
                    "SELECT * FROM workflow_artifacts WHERE artifact_id = ?",
                    (artifact_id,),
                ).fetchone()
            )

    def search_workflow_artifacts(
        self,
        *,
        profile_key: str | None,
        query: str | None,
        tags: list[str],
        path: str | None,
        workflow_key: str | None,
        limit: int,
        task_key: str | None = None,
        task_version: str | None = None,
        run_id: str | None = None,
        include_history: bool = False,
        format: str | None = None,
        path_match: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses, params = self._artifact_search_filters(
            profile_key=profile_key,
            query=query,
            tags=tags,
            path=path,
            workflow_key=workflow_key,
            task_key=task_key,
            task_version=task_version,
            run_id=run_id,
            include_history=include_history,
            format=format,
            path_match=path_match,
        )
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        from_sql, order_by = self._artifact_search_source(query)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT a.* FROM {from_sql}
                {where}
                ORDER BY {order_by}
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
        return [item for row in rows if (item := _row_payload(row)) is not None]

    def search_workflow_artifacts_page(
        self,
        *,
        profile_key: str | None,
        query: str | None,
        tags: list[str],
        path: str | None,
        workflow_key: str | None,
        limit: int,
        offset: int = 0,
        task_key: str | None = None,
        task_version: str | None = None,
        run_id: str | None = None,
        include_history: bool = False,
        format: str | None = None,
        path_match: str | None = None,
    ) -> dict[str, Any]:
        clauses, params = self._artifact_search_filters(
            profile_key=profile_key,
            query=query,
            tags=tags,
            path=path,
            workflow_key=workflow_key,
            task_key=task_key,
            task_version=task_version,
            run_id=run_id,
            include_history=include_history,
            format=format,
            path_match=path_match,
        )
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        bounded_limit = min(max(limit, 1), 50)
        bounded_offset = max(offset, 0)
        from_sql, order_by = self._artifact_search_source(query)
        with self._connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) AS total FROM {from_sql} {where}",
                params,
            ).fetchone()["total"]
            rows = conn.execute(
                f"""
                SELECT a.* FROM {from_sql}
                {where}
                ORDER BY {order_by}
                LIMIT ? OFFSET ?
                """,
                (*params, bounded_limit, bounded_offset),
            ).fetchall()
        items = [item for row in rows if (item := _row_payload(row)) is not None]
        return {
            "items": items,
            "total": int(total),
            "limit": bounded_limit,
            "offset": bounded_offset,
        }

    @staticmethod
    def _artifact_search_filters(
        *,
        profile_key: str | None,
        query: str | None,
        tags: list[str],
        path: str | None,
        workflow_key: str | None,
        task_key: str | None,
        task_version: str | None,
        run_id: str | None,
        include_history: bool,
        format: str | None,
        path_match: str | None = None,
    ) -> tuple[list[str], list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if not include_history:
            clauses.append("a.is_current = 1")
        if profile_key:
            clauses.append("a.profile_key = ?")
            params.append(profile_key)
        if workflow_key:
            clauses.append("a.workflow_key = ?")
            params.append(workflow_key)
        if task_key:
            clauses.append("a.task_key = ?")
            params.append(task_key)
        if task_version is not None:
            clauses.append("a.task_version = ?")
            params.append(task_version)
        if run_id:
            clauses.append("a.run_id = ?")
            params.append(run_id)
        if path:
            clauses.append("a.path LIKE ?")
            params.append(f"{path}%")
        # 路径模糊匹配：同时覆盖 task_key 与产物 path 的子串命中，供前端
        # 产物检索页使用。与上面的 ``path`` 前缀匹配是两套独立条件，后者
        # 仍由 MCP 的 artifacts_search 按前缀+精确语义使用，不可改动。
        if path_match:
            like = f"%{path_match}%"
            clauses.append("(a.task_key LIKE ? OR a.path LIKE ?)")
            params.extend([like, like])
        # Format filter: by default (None) only markdown is returned so that
        # derived artifacts like HTML reports never leak into agent retrieval.
        # Pass format="all" (or "") to disable the filter.
        if format and format != "all":
            clauses.append("a.format = ?")
            params.append(format)
        elif format is None:
            clauses.append("a.format = 'markdown'")
        for tag in tags:
            clauses.append(
                "EXISTS (SELECT 1 FROM json_each(a.tags_json) WHERE json_each.value = ?)"
            )
            params.append(str(tag))
        fts_query = build_artifact_fts_query(query)
        if fts_query is not None:
            clauses.append("workflow_artifacts_fts MATCH ?")
            params.append(fts_query)
        return clauses, params
