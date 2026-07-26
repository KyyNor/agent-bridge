"""Profile 范围的多来源轻量检索探测 API。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field


class RetrievalProbeRequest(BaseModel):
    profile_key: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    session_id: str = ""
    keyword_limit: int = Field(default=8, ge=1, le=32)
    result_limit: int = Field(default=3, ge=1, le=20)
    timeout_seconds: float = Field(default=10.0, ge=0.1, le=30.0)


def create_retrieval_probe_routes(service, actor) -> APIRouter:
    router = APIRouter()

    @router.post("/retrieval/probe")
    async def probe(
        payload: RetrievalProbeRequest,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        result = await service.retrieval_probe.probe(
            actor=current_actor,
            **payload.model_dump(),
        )
        return result.to_payload()

    return router
