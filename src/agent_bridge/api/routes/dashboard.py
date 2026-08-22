"""平台概览聚合接口。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query


def create_dashboard_routes(service, actor):
    router = APIRouter(prefix="/dashboard")

    @router.get("/overview")
    def get_dashboard_overview(
        created_from: str = Query(min_length=10, max_length=32),
        created_to: str = Query(min_length=10, max_length=32),
        refresh: bool = False,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        return service.dashboard.overview(
            actor=current_actor,
            created_from=created_from,
            created_to=created_to,
            refresh=refresh,
        )

    return router
