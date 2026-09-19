"""DSH Web Runtime 的用户态与管理态路由。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from agent_bridge.api.schemas import DshGroupConfigUpdateRequest, DshRuntimeConfigUpdateRequest


def create_dsh_routes(service, actor) -> APIRouter:
    router = APIRouter(prefix="/dsh", tags=["dsh"])

    @router.get("/runtime")
    def get_runtime(current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.dsh.runtime_status(current_actor)

    @router.post("/runtime/ensure")
    def ensure_runtime(current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.dsh.ensure_running(current_actor)

    @router.post("/runtime/stop")
    def stop_runtime(current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.dsh.stop_runtime(current_actor)

    @router.get("/runtimes")
    def list_runtimes(current_actor: str = Depends(actor)) -> dict[str, Any]:
        return {"runtimes": service.dsh.list_runtimes(current_actor)}

    @router.get("/group-configs")
    def list_group_configs(current_actor: str = Depends(actor)) -> dict[str, Any]:
        return {"configs": service.dsh_configs.list_group_configs(current_actor)}

    @router.put("/group-configs/{group_key}")
    def save_group_config(
        group_key: str,
        payload: DshGroupConfigUpdateRequest,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        return service.dsh_configs.save_group_config(
            current_actor,
            group_key=group_key,
            linux_user=payload.linux_user,
            default_model=payload.default_model,
            api_key=payload.api_key,
            clear_api_key=payload.clear_api_key,
            expected_edit_token=payload.expected_edit_token,
        )

    @router.get("/runtime-config")
    def get_runtime_config(current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.dsh_configs.get_runtime_config(current_actor)

    @router.put("/runtime-config")
    def save_runtime_config(
        payload: DshRuntimeConfigUpdateRequest,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        return service.dsh_configs.save_runtime_config(
            current_actor,
            web_command=payload.web_command,
            idle_timeout_minutes=payload.idle_timeout_minutes,
            base_url=payload.base_url,
            available_models=payload.available_models,
            expected_edit_token=payload.expected_edit_token,
        )

    return router
