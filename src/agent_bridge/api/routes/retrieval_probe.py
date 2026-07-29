"""Profile 范围的多来源轻量检索探测 API。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from agent_bridge.api.schemas import ClaudeCodeHookRequest


class RetrievalProbeRequest(BaseModel):
    profile_key: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    session_id: str = ""
    keyword_limit: int = Field(default=8, ge=0, le=8)
    result_limit: int = Field(default=3, ge=1, le=20)
    timeout_seconds: float = Field(default=20.0, gt=0, le=20.0)


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

    @router.post("/retrieval/hooks/claude-code/full-probe")
    async def handle_full_probe_hook(
        payload: ClaudeCodeHookRequest,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        return await service.retrieval_probe.handle_claude_code_hook(
            actor=current_actor,
            profile_key=payload.profile_key,
            event_name=payload.event_name,
            matcher=payload.matcher,
            payload=payload.payload,
            timeout_seconds=payload.hook_timeout_seconds,
        )

    return router
