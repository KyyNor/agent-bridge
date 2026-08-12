"""产品首次使用导览领域服务。"""

from __future__ import annotations

from typing import Any

from agent_bridge.core.domain import ValidationError
from agent_bridge.core.timeutil import utc_iso, utc_now
from agent_bridge.storage.sqlite import SQLiteStore

TOUR_STATUSES = frozenset({"completed", "skipped"})


class OnboardingService:
    def __init__(self, *, store: SQLiteStore) -> None:
        self.store = store

    def get_tour_status(
        self, *, actor: str, tour_key: str, tour_version: int
    ) -> dict[str, Any]:
        self._validate_tour(tour_key=tour_key, tour_version=tour_version)
        record = self.store.get_onboarding_tour_status(
            actor=actor, tour_key=tour_key, tour_version=tour_version
        )
        return {
            "tour_key": tour_key,
            "tour_version": tour_version,
            "status": record["status"] if record else None,
            "updated_at": record["updated_at"] if record else None,
            "should_show": record is None,
        }

    def set_tour_status(
        self, *, actor: str, tour_key: str, tour_version: int, status: str
    ) -> dict[str, Any]:
        self._validate_tour(tour_key=tour_key, tour_version=tour_version)
        if status not in TOUR_STATUSES:
            raise ValidationError("导览状态只能是 completed 或 skipped")
        record = self.store.upsert_onboarding_tour_status(
            actor=actor,
            tour_key=tour_key,
            tour_version=tour_version,
            status=status,
            updated_at=utc_iso(utc_now()),
        )
        return {**record, "should_show": False}

    @staticmethod
    def _validate_tour(*, tour_key: str, tour_version: int) -> None:
        if not tour_key or len(tour_key) > 120:
            raise ValidationError("导览标识不能为空且长度不能超过 120")
        if tour_version < 1:
            raise ValidationError("导览版本必须大于 0")
