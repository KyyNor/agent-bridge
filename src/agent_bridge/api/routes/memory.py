from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends

from agent_bridge.api.schemas import (
    ClaudeCodeHookRequest,
    CreateMemoryBlockRequest,
    ProfileMemoryBindingRequest,
    UpdateMemoryBlockStatusRequest,
)


def _memory_block_payload(block: dict[str, Any]) -> dict[str, Any]:
    payload = dict(block)
    raw_health = payload.get("last_health_json")
    if isinstance(raw_health, str):
        try:
            last_health = json.loads(raw_health) if raw_health else {}
        except json.JSONDecodeError:
            last_health = {}
    elif isinstance(raw_health, dict):
        last_health = raw_health
    else:
        last_health = {}
    payload["last_health"] = last_health
    return payload


def create_memory_routes(service, actor):
    router = APIRouter()

    @router.get("/memory/blocks")
    def list_memory_blocks(current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        return [_memory_block_payload(block) for block in service.memory.list_blocks(current_actor)]

    @router.post("/memory/blocks")
    def create_memory_block(
        payload: CreateMemoryBlockRequest,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        return _memory_block_payload(
            service.memory.create_block(current_actor, payload.block_key, payload.name, payload.description)
        )

    @router.get("/memory/blocks/{block_key}")
    def get_memory_block(block_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return _memory_block_payload(service.memory.get_block(current_actor, block_key))

    @router.post("/memory/blocks/{block_key}/status")
    def update_memory_block_status(
        block_key: str,
        payload: UpdateMemoryBlockStatusRequest,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        return _memory_block_payload(service.memory.set_block_status(current_actor, block_key, payload.status))

    @router.post("/memory/blocks/{block_key}/delete")
    def delete_memory_block(block_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.memory.delete_block(current_actor, block_key)

    @router.get("/memory/blocks/{block_key}/health")
    def get_memory_block_health(block_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.memory.block_health(current_actor, block_key)

    @router.get("/memory/blocks/{block_key}/dashboard")
    def memory_dashboard_status(block_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.memory.dashboard_status(current_actor, block_key)

    @router.post("/memory/blocks/{block_key}/dashboard/start")
    def start_memory_dashboard(block_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.memory.start_dashboard(current_actor, block_key)

    @router.post("/memory/blocks/{block_key}/dashboard/stop")
    def stop_memory_dashboard(block_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.memory.stop_dashboard(current_actor, block_key)

    @router.post("/memory/blocks/{block_key}/dashboard/touch")
    def touch_memory_dashboard(block_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.memory.touch_dashboard(current_actor, block_key)

    @router.get("/memory/blocks/{block_key}/search")
    def search_memory_block(
        block_key: str,
        q: str,
        limit: int = 10,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        return service.memory.search(actor=current_actor, profile_key=None, block_key=block_key, query=q, limit=limit)

    @router.get("/memory/blocks/{block_key}/timeline")
    def memory_block_timeline(
        block_key: str,
        limit: int = 20,
        cursor: str | None = None,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        return service.memory.timeline(
            actor=current_actor,
            profile_key=None,
            block_key=block_key,
            limit=limit,
            cursor=cursor,
        )

    @router.get("/memory/blocks/{block_key}/observations/{observation_id}")
    def get_memory_observation(
        block_key: str,
        observation_id: str,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        return service.memory.get_observation(
            actor=current_actor,
            profile_key=None,
            block_key=block_key,
            observation_id=observation_id,
        )

    @router.get("/capability-profiles/{profile_key}/memory")
    def get_profile_memory(profile_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.memory.get_profile_binding(current_actor, profile_key)

    @router.put("/capability-profiles/{profile_key}/memory")
    def set_profile_memory(
        profile_key: str,
        payload: ProfileMemoryBindingRequest,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        return service.memory.set_profile_binding(
            current_actor,
            profile_key,
            payload.block_key,
            enabled=payload.enabled,
            expected_edit_token=payload.expected_edit_token,
        )

    @router.post("/memory/hooks/claude-code/{action}")
    def handle_claude_code_memory_hook(
        action: str,
        payload: ClaudeCodeHookRequest,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        return service.memory.hooks.handle_claude_code_hook(
            actor=current_actor,
            profile_key=payload.profile_key,
            action=action,
            event_name=payload.event_name,
            matcher=payload.matcher,
            payload=payload.payload,
            timeout_seconds=payload.hook_timeout_seconds,
        )

    return router
