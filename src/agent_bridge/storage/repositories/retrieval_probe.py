"""全量检索探测的小模型全局配置。"""

from __future__ import annotations

from typing import Any

from agent_bridge.core.timeutil import utc_iso


class RetrievalProbeConfigRepository:
    def __init__(self, db_path, connect) -> None:
        self._db_path = db_path
        self._connect = connect

    def get_llm_config(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT base_url, api_key, model, updated_at FROM retrieval_probe_llm_config WHERE id = 1"
            ).fetchone()
        if row is None:
            return {"base_url": "", "api_key": "", "model": "", "updated_at": None}
        return dict(row)

    def save_llm_config(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None,
        clear_api_key: bool,
    ) -> dict[str, Any]:
        existing = self.get_llm_config()
        resolved_key = "" if clear_api_key else (api_key if api_key else existing["api_key"])
        updated_at = utc_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO retrieval_probe_llm_config (id, base_url, api_key, model, updated_at)
                VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  base_url = excluded.base_url,
                  api_key = excluded.api_key,
                  model = excluded.model,
                  updated_at = excluded.updated_at
                """,
                (base_url, resolved_key, model, updated_at),
            )
        return self.get_llm_config()
