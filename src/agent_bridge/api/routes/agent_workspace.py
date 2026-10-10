"""Agent 工作台的用户态与管理态路由。

用户态接口按 ``agent`` 参数（``dsh`` / ``pi``）泛化到统一的
``/api/v1/agent-workspace/*``；``/api/v1/dsh/*`` 作为 DSH 别名保留至少一个
版本，语义等价于 ``agent=dsh``。

两份路由**共用同一批处理器**（:func:`create_agent_workspace_handlers` 返回的
命名空间），别名只是把 ``agent`` 固定为 ``dsh``，避免定义重复后各自漂移。

管理接口（组级/全局配置）沿用 DSH 的分层结构：组级 ``linux_user`` /
``default_model`` / ``api_key`` 与公共 Base URL、可用模型由两种 Agent 共享，
避免重复配置。
"""

from __future__ import annotations

from types import SimpleNamespace
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


def create_agent_workspace_handlers(service, actor) -> SimpleNamespace:
    """构造工作台路由处理器；泛化路由与 DSH 别名共用同一批。"""

    def list_agents(current_actor: str = Depends(actor)) -> dict[str, Any]:
        """可选的 Coding Agent 目录，供进入页渲染选择卡片。

        必须登录：与其他工作台用户态接口一致，未识别调用者返回 401。
        """
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

    def get_runtime(
        agent: str = "dsh",
        scope: str = "personal",
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        return service.workspace_runtime(agent).runtime_status(current_actor, scope=scope)

    def authorize_workspace(
        payload: DshWorkspaceAuthorizeRequest,
        agent: str = "dsh",
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        return service.workspace_runtime(agent).authorize_workspace(
            current_actor, profile_key=payload.profile_key, scope=payload.scope
        )

    def ensure_runtime(
        agent: str = "dsh",
        scope: str = "personal",
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        return service.workspace_runtime(agent).ensure_running(current_actor, scope=scope)

    def stop_runtime(
        agent: str = "dsh",
        scope: str = "personal",
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        return service.workspace_runtime(agent).stop_runtime(current_actor, scope=scope)

    def list_runtimes(agent: str = "", current_actor: str = Depends(actor)) -> dict[str, Any]:
        """管理端实例列表；``agent`` 为空时返回全部 Agent。"""
        if agent:
            return {"runtimes": service.workspace_runtime(agent).list_runtimes(current_actor)}
        runtimes: list[dict[str, Any]] = []
        for runtime in service.workspace_agents.values():
            runtimes.extend(runtime.list_runtimes(current_actor))
        return {"runtimes": runtimes}

    # -- 管理态：配置（两种 Agent 共享同一套组级/全局配置）--

    def list_group_configs(current_actor: str = Depends(actor)) -> dict[str, Any]:
        return {"configs": service.dsh_configs.list_group_configs(current_actor)}

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

    def get_runtime_config(current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.dsh_configs.get_runtime_config(current_actor)

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

    def get_pi_versions(current_actor: str = Depends(actor)) -> dict[str, Any]:
        """托管的 Pi 依赖版本清单（钉死精确版本，随 Agent Bridge 发布）。"""
        return {
            "packages": read_version_manifest(),
            "default_web_command": DEFAULT_PI_WEB_COMMAND,
            "node_engine": ">=22.19.0",
        }

    return SimpleNamespace(
        list_agents=list_agents,
        get_runtime=get_runtime,
        authorize_workspace=authorize_workspace,
        ensure_runtime=ensure_runtime,
        stop_runtime=stop_runtime,
        list_runtimes=list_runtimes,
        list_group_configs=list_group_configs,
        save_group_config=save_group_config,
        get_runtime_config=get_runtime_config,
        save_runtime_config=save_runtime_config,
        get_pi_versions=get_pi_versions,
    )


def _dsh_only(handler, *, agent: str = "dsh"):
    """把泛化处理器绑定到固定的 agent（供别名路由复用）。"""
    if agent != "dsh":  # pragma: no cover - 别名只固定 dsh
        return handler

    def bound(*args, **kwargs):
        kwargs.setdefault("agent", agent)
        return handler(*args, **kwargs)

    # 保留签名供 FastAPI 解析依赖
    import inspect

    bound.__signature__ = inspect.signature(handler)  # type: ignore[attr-defined]
    bound.__annotations__ = dict(handler.__annotations__)
    bound.__defaults__ = handler.__defaults__
    return bound


def create_agent_workspace_routes(service, actor) -> APIRouter:
    """统一路由 ``/api/v1/agent-workspace/*``（带 ``agent`` 参数）。"""
    handlers = create_agent_workspace_handlers(service, actor)
    router = APIRouter(prefix="/agent-workspace", tags=["agent-workspace"])
    router.add_api_route("/agents", handlers.list_agents, methods=["GET"])
    router.add_api_route("/runtime", handlers.get_runtime, methods=["GET"])
    router.add_api_route("/workspace/authorize", handlers.authorize_workspace, methods=["POST"])
    router.add_api_route("/runtime/ensure", handlers.ensure_runtime, methods=["POST"])
    router.add_api_route("/runtime/stop", handlers.stop_runtime, methods=["POST"])
    router.add_api_route("/runtimes", handlers.list_runtimes, methods=["GET"])
    router.add_api_route("/group-configs", handlers.list_group_configs, methods=["GET"])
    router.add_api_route(
        "/group-configs/{group_key}", handlers.save_group_config, methods=["PUT"]
    )
    router.add_api_route("/runtime-config", handlers.get_runtime_config, methods=["GET"])
    router.add_api_route("/runtime-config", handlers.save_runtime_config, methods=["PUT"])
    router.add_api_route("/pi-versions", handlers.get_pi_versions, methods=["GET"])
    return router


def create_dsh_routes(service, actor) -> APIRouter:
    """``/api/v1/dsh/*``：DSH 专属别名，语义等价于 ``agent=dsh``。

    保留以便既有书签、前端缓存与外部脚本在泛化路由上至少一个版本内继续可用。
    处理器与 :func:`create_agent_workspace_routes` 完全共用。
    """
    handlers = create_agent_workspace_handlers(service, actor)
    router = APIRouter(prefix="/dsh", tags=["dsh"])
    router.add_api_route("/runtime", _dsh_only(handlers.get_runtime), methods=["GET"])
    router.add_api_route(
        "/workspace/authorize", handlers.authorize_workspace, methods=["POST"]
    )
    router.add_api_route("/runtime/ensure", _dsh_only(handlers.ensure_runtime), methods=["POST"])
    router.add_api_route("/runtime/stop", _dsh_only(handlers.stop_runtime), methods=["POST"])
    router.add_api_route("/runtimes", _dsh_only(handlers.list_runtimes), methods=["GET"])
    router.add_api_route("/group-configs", handlers.list_group_configs, methods=["GET"])
    router.add_api_route(
        "/group-configs/{group_key}", handlers.save_group_config, methods=["PUT"]
    )
    router.add_api_route("/runtime-config", handlers.get_runtime_config, methods=["GET"])
    router.add_api_route("/runtime-config", handlers.save_runtime_config, methods=["PUT"])
    return router