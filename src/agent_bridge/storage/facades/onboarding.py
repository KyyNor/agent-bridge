"""产品导览状态的兼容门面。"""

from __future__ import annotations

from typing import Any


class OnboardingFacadeMixin:
    def get_onboarding_tour_status(
        self, *, actor: str, tour_key: str, tour_version: int
    ) -> dict[str, Any] | None:
        return self.onboarding.get_tour_status(
            actor=actor, tour_key=tour_key, tour_version=tour_version
        )

    def upsert_onboarding_tour_status(
        self,
        *,
        actor: str,
        tour_key: str,
        tour_version: int,
        status: str,
        updated_at: str,
    ) -> dict[str, Any]:
        return self.onboarding.upsert_tour_status(
            actor=actor,
            tour_key=tour_key,
            tour_version=tour_version,
            status=status,
            updated_at=updated_at,
        )
