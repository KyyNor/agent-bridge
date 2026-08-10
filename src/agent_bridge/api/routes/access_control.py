"""小组与用户映射接口。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from agent_bridge.api.schemas import SetUserGroupRequest, UpsertAccessGroupRequest


def create_access_control_routes(service, actor):
    router = APIRouter(prefix="/access", tags=["access-control"])

    @router.get("/me")
    def get_actor_context(current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.access.actor_context(current_actor)

    @router.get("/groups")
    def list_groups(current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        return service.access.list_groups(current_actor)

    @router.post("/groups")
    def upsert_group(
        payload: UpsertAccessGroupRequest,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        return service.access.upsert_group(actor=current_actor, **payload.model_dump())

    @router.get("/memberships")
    def list_memberships(current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        return service.access.list_memberships(current_actor)

    @router.put("/memberships")
    def set_membership(
        payload: SetUserGroupRequest,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        return service.access.set_user_group(actor=current_actor, **payload.model_dump())

    @router.delete("/memberships/{user_id}")
    def delete_membership(
        user_id: str,
        current_actor: str = Depends(actor),
    ) -> dict[str, bool]:
        return service.access.remove_user_group(actor=current_actor, user_id=user_id)

    return router
