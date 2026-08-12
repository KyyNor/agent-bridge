"""产品导览状态 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from agent_bridge.api.schemas import OnboardingTourStatusRequest


def create_onboarding_routes(service, actor):
    router = APIRouter(prefix="/onboarding", tags=["onboarding"])

    @router.get("/tours/{tour_key}")
    def get_tour_status(
        tour_key: str,
        version: int = Query(ge=1),
        current_actor: str = Depends(actor),
    ) -> dict:
        return service.onboarding.get_tour_status(
            actor=current_actor, tour_key=tour_key, tour_version=version
        )

    @router.put("/tours/{tour_key}")
    def set_tour_status(
        tour_key: str,
        payload: OnboardingTourStatusRequest,
        current_actor: str = Depends(actor),
    ) -> dict:
        return service.onboarding.set_tour_status(
            actor=current_actor,
            tour_key=tour_key,
            tour_version=payload.version,
            status=payload.status,
        )

    return router
