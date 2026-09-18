"""DSH Web Runtime 的组级与全局运行配置持久化。"""

from __future__ import annotations

import json
from typing import Any

from agent_bridge.core.timeutil import utc_iso


def _decode_models(raw: str | None) -> list[str]:
    try:
        payload = json.loads(raw or "[]")
    except Exception:
        return []
    if not isinstance(payload, list):
        return []
    return [str(item) for item in payload]


class DshConfigRepository:
    def __init__(self, db_path, connect) -> None:
        self._db_path = db_path
        self._connect = connect

    # -- 组级配置 --

    def _row_to_config(self, row: Any) -> dict[str, Any]:
        config = dict(row)
        config["available_models"] = _decode_models(config.pop("available_models_json", None))
        return config

    def get_group_config(self, group_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT group_key, linux_user, base_url, default_model,
                       available_models_json, api_key, updated_by, updated_at
                FROM dsh_group_configs WHERE group_key = ?
                """,
                (group_key,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_config(row)

    def list_group_configs(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT group_key, linux_user, base_url, default_model,
                       available_models_json, api_key, updated_by, updated_at
                FROM dsh_group_configs ORDER BY group_key
                """
            ).fetchall()
        return [self._row_to_config(row) for row in rows]

    def save_group_config(
        self,
        *,
        group_key: str,
        linux_user: str,
        base_url: str,
        default_model: str,
        available_models: list[str],
        api_key: str | None,
        clear_api_key: bool,
        updated_by: str,
    ) -> dict[str, Any]:
        existing = self.get_group_config(group_key) or {}
        resolved_key = "" if clear_api_key else (api_key if api_key else str(existing.get("api_key") or ""))
        updated_at = utc_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO dsh_group_configs (
                  group_key, linux_user, base_url, default_model,
                  available_models_json, api_key, updated_by, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(group_key) DO UPDATE SET
                  linux_user = excluded.linux_user,
                  base_url = excluded.base_url,
                  default_model = excluded.default_model,
                  available_models_json = excluded.available_models_json,
                  api_key = excluded.api_key,
                  updated_by = excluded.updated_by,
                  updated_at = excluded.updated_at
                """,
                (
                    group_key,
                    linux_user,
                    base_url,
                    default_model,
                    json.dumps(available_models, ensure_ascii=False),
                    resolved_key,
                    updated_by,
                    updated_at,
                ),
            )
        return self.get_group_config(group_key) or {}

    def delete_group_config(self, group_key: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM dsh_group_configs WHERE group_key = ?", (group_key,))
            return cursor.rowcount > 0

    # -- 全局运行配置 --

    def get_runtime_config(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT web_command, idle_timeout_minutes, updated_at
                FROM dsh_runtime_config WHERE id = 1
                """
            ).fetchone()
        if row is None:
            return {"web_command": "", "idle_timeout_minutes": 0, "updated_at": None}
        return dict(row)

    def save_runtime_config(self, *, web_command: str, idle_timeout_minutes: int) -> dict[str, Any]:
        updated_at = utc_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO dsh_runtime_config (id, web_command, idle_timeout_minutes, updated_at)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  web_command = excluded.web_command,
                  idle_timeout_minutes = excluded.idle_timeout_minutes,
                  updated_at = excluded.updated_at
                """,
                (web_command, idle_timeout_minutes, updated_at),
            )
        return self.get_runtime_config()
