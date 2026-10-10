"""Agent 工作台的用户态与管理态路由。

用户态接口按 ``agent`` 参数（``dsh`` / ``pi``）泛化到统一的
``/api/v1/agent-workspace/*``；``/api/v1/dsh/*`` 作为 DSH 别名保留，至少一个
版本，避免既有书签与前端缓存失效。

管理接口（组级/全局配置）沿用 DSH 的分层结构：组级 ``linux_user`` /
``default_model`` / ``api_key`` 与公共 Base URL、可用模型由两种 Agent 共享，
避免重复配置。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from agent_bridge.agent_workspace.pi_versions import (
    DEFAULT_PI_WEB_COMMAND,
    read_version_manifest,
)
from agent_bridge.api.schemas import (
    DshGroupConfigUpdateRequest,
    DshRuntimeConfigUpdateRequest,
    DshWorkspaceAuthorizeRequest,
)


def _agent_param(agent: str) -> str:
    from agent_bridge.agent_workspace.scopes import normalize_workspace_agent

    return normalize_workspace_agent(agent)


def create_agent_workspace_routes(service, actor) -> APIRouter:
    router = APIRouter(prefix="/agent-workspace", tags=["agent-workspace"])

    @router.get("/agents")
    def list_agents() -> dict[str, Any]:
        """可选的 Coding Agent 目录，供进入页渲染选择卡片。"""
        return {
            "agents": [
                {
                    "agent": runtime.adapter.slug,
                    "display_name": runtime.adapter.display_name,
                    "workspace_url": runtime.workspace_url("personal"),
                    "shared_workspace_url": runtime.workspace_url("shared"),
                    "supports_shared": runtime.adapter.supports_scope("shared"),
                }
                for runtime in service.workspace_agents.values()
            ]
        }

    @router.get("/runtime")
    def get_runtime(
        agent: str = "dsh",
        scope: str = "personal",
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        return service.workspace_runtime(agent).runtime_status(current_actor, scope=scope)

    @router.post("/workspace/authorize")
    def authorize_workspace(
        payload: DshWorkspaceAuthorizeRequest,
        agent: str = "dsh",
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        return service.workspace_runtime(agent).authorize_workspace(
            current_actor, profile_key=payload.profile_key, scope=payload.scope
        )

    @router.post("/runtime/ensure")
    def ensure_runtime(
        agent: str = "dsh",
        scope: str = "personal",
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        return service.workspace_runtime(agent).ensure_running(current_actor, scope=scope)

    @router.post("/runtime/stop")
    def stop_runtime(
        agent: str = "dsh",
        scope: str = "personal",
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        return service.workspace_runtime(agent).stop_runtime(current_actor, scope=scope)

    @router.get("/runtimes")
    def list_runtimes(
        agent: str = "", current_actor: str = Depends(actor)
    ) -> dict[str, Any]:
        """管理端实例列表；``agent`` 为空时返回全部 Agent。"""
        if agent:
            return {"runtimes": service.workspace_runtime(agent).list_runtimes(current_actor)}
        runtimes: list[dict[str, Any]] = []
        for runtime in service.workspace_agents.values():
            runtimes.extend(runtime.list_runtimes(current_actor))
        return {"runtimes": runtimes}

    # -- 管理态：配置（两种 Agent 共享同一套组级/全局配置）--

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
            pi_web_command=payload.pi_web_command,
            idle_timeout_minutes=payload.idle_timeout_minutes,
            base_url=payload.base_url,
            available_models=payload.available_models,
            expected_edit_token=payload.expected_edit_token,
        )

    @router.get("/pi-versions")
    def get_pi_versions(current_actor: str = Depends(actor)) -> dict[str, Any]:
        """托管的 Pi 依赖版本清单（钉死精确版本，随 Agent Bridge 发布）。"""
        return {
            "packages": read_version_manifest(),
            "default_web_command": DEFAULT_PI_WEB_COMMAND,
            "node_engine": ">=22.19.0",
        }

    return router


def create_dsh_routes(service, actor) -> APIRouter:
    """``/api/v1/dsh/*``：DSH 专属别名，等价于 ``agent=dsh``。

    保留以便既有书签、前端缓存与外部脚本在泛化路由上至少一个版本内继续可用。
    """

    def _dsh_authorize(payload: DshWorkspaceAuthorizeRequest, current_actor: str) -> dict[str, Any]:
        return service.dsh.authorize_workspace(
            current_actor, profile_key=payload.profile_key, scope=payload.scope
        )

    router = APIRouter(prefix="/dsh", tags=["dsh"])

    @router.get("/runtime")
    def get_runtime(
        scope: str = "personal", current_actor: str = Depends(actor)
    ) -> dict[str, Any]:
        return service.dsh.runtime_status(current_actor, scope=scope)

    @router.post("/workspace/authorize")
    def authorize_workspace(
        payload: DshWorkspaceAuthorizeRequest,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        return _dsh_authorize(payload, current_actor)

    @router.post("/runtime/ensure")
    def ensure_runtime(
        scope: str = "personal", current_actor: str = Depends(actor)
    ) -> dict[str, Any]:
        return service.dsh.ensure_running(current_actor, scope=scope)

    @router.post("/runtime/stop")
    def stop_runtime(
        scope: str = "personal", current_actor: str = Depends(actor)
    ) -> dict[str, Any]:
        return service.dsh.stop_runtime(current_actor, scope=scope)

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
            pi_web_command=payload.pi_web_command,
            idle_timeout_minutes=payload.idle_timeout_minutes,
            base_url=payload.base_url,
            available_models=payload.available_models,
            expected_edit_token=payload.expected_edit_token,
        )

    return router