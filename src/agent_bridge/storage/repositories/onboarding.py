"""按用户保存产品导览进度的 SQLite 仓储。"""

from __future__ import annotations

from typing import Any


class OnboardingRepository:
    def __init__(self, db_path, connect) -> None:
        self._db_path = db_path
        self._connect = connect

    def get_tour_status(
        self, *, actor: str, tour_key: str, tour_version: int
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT actor, tour_key, tour_version, status, updated_at
                FROM onboarding_tour_statuses
                WHERE actor = ? AND tour_key = ? AND tour_version = ?
                """,
                (actor, tour_key, tour_version),
            ).fetchone()
        return dict(row) if row else None

    def upsert_tour_status(
        self,
        *,
        actor: str,
        tour_key: str,
        tour_version: int,
        status: str,
        updated_at: str,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO onboarding_tour_statuses (
                  actor, tour_key, tour_version, status, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(actor, tour_key, tour_version) DO UPDATE SET
                  status = excluded.status,
                  updated_at = excluded.updated_at
                """,
                (actor, tour_key, tour_version, status, updated_at),
            )
        result = self.get_tour_status(
            actor=actor, tour_key=tour_key, tour_version=tour_version
        )
        if result is None:
            raise RuntimeError("导览状态保存后无法读取")
        return result
