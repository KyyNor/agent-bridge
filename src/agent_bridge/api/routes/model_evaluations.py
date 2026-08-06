"""系统管理中的模型评估 API。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from agent_bridge.api.schemas import ModelEvaluationStartRequest


class ModelListRequest(BaseModel):
    base_url: str = Field(default="", max_length=2048)
    api_key: str = Field(default="", max_length=4096)


def create_model_evaluation_routes(service, actor):
    router = APIRouter(prefix="/model-evaluations")

    @router.get("/datasets")
    def list_datasets(current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        return service.model_evaluations.list_datasets(current_actor)

    @router.get("/runtime")
    def runtime_status(current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.model_evaluations.runtime_status(current_actor)

    @router.post("/models")
    def list_models(
        payload: ModelListRequest,
        current_actor: str = Depends(actor),
    ) -> list[dict[str, str]]:
        return service.model_evaluations.list_models(current_actor, **payload.model_dump())

    @router.get("")
    def list_runs(
        limit: int = Query(default=50, ge=1, le=100),
        current_actor: str = Depends(actor),
    ) -> list[dict[str, Any]]:
        return service.model_evaluations.list_runs(current_actor, limit=limit)

    @router.post("")
    def start_run(
        payload: ModelEvaluationStartRequest,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        return service.model_evaluations.start_run(current_actor, **payload.model_dump())

    @router.get("/{run_id}")
    def get_run(run_id: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.model_evaluations.get_run(current_actor, run_id)

    return router
