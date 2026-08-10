"""Standard MCP endpoint using FastMCP with per-request stateless transport."""

from __future__ import annotations

import inspect
import json
import keyword
import logging
import time
from contextvars import ContextVar
from typing import Annotated, Any

import anyio
from fastapi import APIRouter, Request, Response
from mcp.server.fastmcp import FastMCP
from mcp.server.streamable_http import StreamableHTTPServerTransport
from pydantic import Field

from agent_bridge.access_control.identity import RequestIdentityResolver
from agent_bridge.core.config import default_user
from agent_bridge.app.service import AgentBridgeService
from agent_bridge.capability_hub.gateway.top_level_tools import top_level_mcp_tools

logger = logging.getLogger("agent_bridge.mcp")

_request_profile: ContextVar[str | None] = ContextVar("_request_profile", default=None)
_request_workflow_context: ContextVar[dict[str, Any] | None] = ContextVar("_request_workflow_context", default=None)
_request_actor: ContextVar[str | None] = ContextVar("_request_actor", default=None)


def _active_actor() -> str:
    """返回当前 MCP 请求身份；直接构造 server 的兼容场景沿用进程默认用户。"""
    return _request_actor.get() or default_user()


def _is_top_level_mcp_tool_enabled(bridge_service: Any, tool_name: str) -> bool:
    """兼容未实现工具开关的旧门面；缺少配置能力时沿用默认启用语义。"""
    capabilities = getattr(bridge_service, "capabilities", None)
    checker = getattr(capabilities, "is_top_level_mcp_tool_enabled", None)
    return bool(checker(tool_name)) if callable(checker) else True


def _annotation_from_json_schema(definition: dict[str, Any]) -> Any:
    value_type = definition.get("type")
    if isinstance(value_type, list):
        value_type = next((item for item in value_type if item != "null"), None)
    # Pydantic 把 Optional/联合类型生成为 anyOf/oneOf，很多 MCP 客户端只认顶层 type
    # 字段；这里取联合中非 null 的子 schema，避免最终参数类型显示为 "unknown"。
    if value_type is None:
        for union_key in ("anyOf", "oneOf"):
            union = definition.get(union_key)
            if isinstance(union, list):
                for branch in union:
                    if isinstance(branch, dict) and branch.get("type") != "null":
                        value_type = branch.get("type")
                        break
                if value_type is not None:
                    break
    if value_type == "string":
        return str
    if value_type == "integer":
        return int
    if value_type == "number":
        return float
    if value_type == "boolean":
        return bool
    if value_type == "array":
        return list
    if value_type == "object":
        return dict
    return Any


def _is_safe_schema_property_name(name: str) -> bool:
    return name.isidentifier() and not keyword.iskeyword(name)


def _invalid_json_schema_property_names(schema: dict[str, Any]) -> list[str]:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return []
    return [
        name
        for name in properties
        if not isinstance(name, str) or not _is_safe_schema_property_name(name)
    ]


def _signature_from_json_schema(schema: dict[str, Any]) -> inspect.Signature:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    required = schema.get("required")
    required_names = set(required if isinstance(required, list) else [])
    parameters = []
    for name, definition in properties.items():
        if not isinstance(name, str) or not _is_safe_schema_property_name(name):
            continue
        if not isinstance(definition, dict):
            definition = {}
        default = inspect._empty if name in required_names else definition.get("default", None)
        annotation = _annotation_from_json_schema(definition)
        description = definition.get("description")
        if isinstance(description, str) and description.strip():
            annotation = Annotated[annotation, Field(description=description.strip())]
        parameters.append(
            inspect.Parameter(
                name,
                inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=annotation,
            )
        )
    return inspect.Signature(parameters=parameters, return_annotation=dict[str, Any])


def _has_complete_workflow_context(context: dict[str, Any] | None) -> bool:
    if not context or not context.get("workflow"):
        return False
    return bool(context.get("workflow_key")) and bool(context.get("run_id"))


def create_mcp_server(
    service: AgentBridgeService,
    profile_key: str | None = None,
    workflow_context: dict[str, Any] | None = None,
) -> FastMCP:
    """为单次请求构建一个无状态的 FastMCP 实例。

    每个请求都新建一个临时 server（无进程级单例），per-request 的 profile /
    workflow 上下文通过模块级 ``ContextVar``（``_request_profile`` /
    ``_request_workflow_context``）传递，而非函数参数。除 search/execute 外，
    存在完整 workflow 上下文时还会注册 workflow_get_task 等 3 个辅助工具，
    以及 profile 的 pinned 工具和 wiki/codegraph/memory 直连工具。
    """
    bridge_service = service
    mcp = FastMCP(
        name="agent-bridge",
        instructions=(
            "Agent Capability Hub gateway. "
            "Use search to discover available MCP tools and services, "
            "then use execute to run them."
        ),
    )

    @mcp.tool(
        description="浏览并搜索 Agent Bridge 能力目录。",
    )
    def search(
        path: str = Field(default="", description="要浏览的能力路径；留空时返回当前可见服务列表。"),
        query: str = Field(default="", description="用于过滤当前路径结果的关键词。"),
        limit: int = Field(default=20, description="本次最多返回的结果数量。"),
    ) -> dict[str, Any]:
        active_profile = _request_profile.get() or profile_key
        logger.info("搜索 profile=%s path=%s query=%s limit=%s", active_profile, path, query, limit)
        started = time.monotonic()
        try:
            result = service.capabilities.search(
                actor=_active_actor(),
                path=path,
                query=query,
                limit=limit,
                profile_key=active_profile,
            )
            logger.info("搜索完成 profile=%s 耗时=%.0fms 结果数=%d", active_profile, (time.monotonic() - started) * 1000, len(result.get("items", [])))
            return result
        except Exception as exc:
            logger.error("搜索失败 profile=%s 耗时=%.0fms 错误=%s", active_profile, (time.monotonic() - started) * 1000, exc)
            raise

    @mcp.tool(
        description="执行一个已注册的 Agent Bridge 能力。",
    )
    async def execute(
        service: Annotated[str, Field(description="要调用的服务标识。")],
        tool_name: Annotated[str, Field(description="服务下要执行的工具名称。")],
        params: dict[str, Any] = Field(default_factory=dict, description="传给目标工具的 JSON 参数对象。"),
    ) -> dict[str, Any]:
        active_profile = _request_profile.get() or profile_key
        current_workflow_context = _request_workflow_context.get() or active_workflow_context
        logger.info("执行 profile=%s service=%s tool=%s params=%s", active_profile, service, tool_name, json.dumps(params or {}, ensure_ascii=False))
        started = time.monotonic()
        try:
            result = await bridge_service.capabilities.execute(
                actor=_active_actor(),
                service=service,
                tool_name=tool_name,
                params=params or {},
                profile_key=active_profile,
                workflow_context=current_workflow_context,
            )
            logger.info("执行完成 profile=%s service=%s tool=%s 耗时=%.0fms success=%s", active_profile, service, tool_name, (time.monotonic() - started) * 1000, result.get("success"))
            return result
        except Exception as exc:
            logger.error(
                "执行失败 profile=%s service=%s tool=%s 耗时=%.0fms",
                active_profile,
                service,
                tool_name,
                (time.monotonic() - started) * 1000,
                exc_info=True,
            )
            raise

    def artifacts_search(
        query: str = Field(default="", description="检索关键词。"),
        path: str = Field(default="", description="产物路径前缀；传入完整路径时返回正文。"),
        limit: int = Field(default=20, description="最多返回数量（1-50）。"),
    ) -> dict[str, Any]:
        active_profile = _request_profile.get() or profile_key
        result = bridge_service.capabilities.invoke_logged_tool(
            actor=_active_actor(),
            profile_key=active_profile,
            entrypoint="metamcp_search",
            source_type="builtin",
            source_key="workflow",
            tool_name="artifacts_search",
            request={
                "query": query,
                "path": path,
                "limit": limit,
            },
            handler=lambda: _search_artifacts_if_enabled(
                bridge_service,
                actor=_active_actor(),
                profile_key=active_profile,
                query=query,
                path=path,
                limit=limit,
            ),
        )
        for item in result.get("items", []):
            for field in (
                "artifact_id",
                "workflow_key",
                "profile_key",
                "run_id",
                "task_key",
                "task_version",
                "is_current",
                "format",
                "tags",
            ):
                item.pop(field, None)
        if result.get("items"):
            result["hint"] = "结果默认只含摘要；将完整 path 作为 path 重新调用可获取正文。"
        return result

    if _is_top_level_mcp_tool_enabled(bridge_service, "artifacts_search"):
        mcp.tool(description="搜索当前 profile 的工作流产物。需要全文时，将结果的完整 path 作为 path 重新调用。")(artifacts_search)

    active_workflow_context = workflow_context or _request_workflow_context.get()
    if _has_complete_workflow_context(active_workflow_context):
        logger.info(
            "工作流上下文已注册 workflow=%s run=%s",
            active_workflow_context.get("workflow_key"),
            active_workflow_context.get("run_id"),
        )

        def workflow_get_task() -> dict[str, Any]:
            active_profile = _request_profile.get() or profile_key
            current = _request_workflow_context.get() or active_workflow_context or {}
            workflow_key = str(current.get("workflow_key") or "")
            run_id = str(current.get("run_id") or "")
            return bridge_service.capabilities.invoke_logged_tool(
                actor=_active_actor(),
                profile_key=active_profile,
                entrypoint="metamcp_execute",
                source_type="builtin",
                source_key="workflow",
                tool_name="workflow_get_task",
                request={"workflow_key": workflow_key, "run_id": run_id},
                handler=lambda: _workflow_get_task_if_enabled(
                    bridge_service, active_profile, workflow_key, run_id
                ),
            )

        if _is_top_level_mcp_tool_enabled(bridge_service, "workflow_get_task"):
            mcp.tool(description="领取当前工作流运行中的一个待处理任务。")(workflow_get_task)

        def workflow_set_task(
            tasks: Annotated[list[dict[str, Any]], Field(description="要写入工作流队列的任务列表。")]
        ) -> dict[str, Any]:
            active_profile = _request_profile.get() or profile_key
            current = _request_workflow_context.get() or active_workflow_context or {}
            workflow_key = str(current.get("workflow_key") or "")
            run_id = str(current.get("run_id") or "")
            return bridge_service.capabilities.invoke_logged_tool(
                actor=_active_actor(),
                profile_key=active_profile,
                entrypoint="metamcp_execute",
                source_type="builtin",
                source_key="workflow",
                tool_name="workflow_set_task",
                request={"workflow_key": workflow_key, "run_id": run_id, "tasks": tasks},
                handler=lambda: _workflow_set_task_if_enabled(
                    bridge_service, active_profile, workflow_key, run_id, tasks
                ),
            )

        if _is_top_level_mcp_tool_enabled(bridge_service, "workflow_set_task"):
            mcp.tool(description="创建或刷新当前工作流的待处理任务。")(workflow_set_task)

        def workflow_run_log(
            level: str = Field(default="info", description="日志级别，如 info、warning 或 error。"),
            stage: str = Field(default="", description="日志所属阶段标识。"),
            message: str = Field(default="", description="日志消息正文。"),
            task_key: str = Field(default="", description="关联的任务键；没有可留空。"),
            payload: dict[str, Any] = Field(default_factory=dict, description="附加到日志中的 JSON 结构化载荷。"),
        ) -> dict[str, Any]:
            active_profile = _request_profile.get() or profile_key
            current = _request_workflow_context.get() or active_workflow_context or {}
            workflow_key = str(current.get("workflow_key") or "")
            run_id = str(current.get("run_id") or "")
            return bridge_service.capabilities.invoke_logged_tool(
                actor=_active_actor(),
                profile_key=active_profile,
                entrypoint="metamcp_execute",
                source_type="builtin",
                source_key="workflow",
                tool_name="workflow_run_log",
                request={
                    "workflow_key": workflow_key,
                    "run_id": run_id,
                    "task_key": task_key,
                    "level": level,
                    "stage": stage,
                    "message": message,
                    "payload": payload or {},
                },
                handler=lambda: _append_workflow_run_log_if_enabled(
                    service=service,
                    bridge_service=bridge_service,
                    profile_key=active_profile,
                    workflow_key=workflow_key,
                    run_id=run_id,
                    task_key=task_key,
                    level=level,
                    stage=stage,
                    message=message,
                    payload=payload or {},
                ),
            )

        if _is_top_level_mcp_tool_enabled(bridge_service, "workflow_run_log"):
            mcp.tool(description="追加一条当前工作流运行日志。")(workflow_run_log)

    def register_direct_builtin_tools() -> None:
        providers = getattr(service.capabilities, "builtin_providers", {})
        registered_names = {"search", "execute", "artifacts_search"}
        for direct_spec in (spec for spec in top_level_mcp_tools() if spec.kind == "direct_builtin"):
            name = direct_spec.name
            if name in registered_names:
                continue
            registered_names.add(name)
            if not _is_top_level_mcp_tool_enabled(bridge_service, name):
                continue
            provider = providers.get(direct_spec.service_key) if isinstance(providers, dict) else None
            if provider is None:
                continue
            builtin_tools = {
                tool.tool: tool
                for tool in provider.list_tools(_active_actor(), profile_key)
            }
            builtin_tool = builtin_tools.get(direct_spec.tool_name)
            if builtin_tool is None:
                continue
            invalid_parameter_names = _invalid_json_schema_property_names(builtin_tool.input_schema)
            if invalid_parameter_names:
                logger.warning(
                    "跳过 direct builtin MCP tool name=%s invalid_schema_fields=%s",
                    name,
                    invalid_parameter_names,
                )
                continue

            async def direct_builtin_tool(_spec: Any = direct_spec, **kwargs: Any) -> dict[str, Any]:
                active_profile = _request_profile.get() or profile_key
                current_workflow_context = _request_workflow_context.get() or active_workflow_context
                return await service.capabilities.execute(
                    actor=_active_actor(),
                    service=_spec.service_key,
                    tool_name=_spec.tool_name,
                    params=kwargs,
                    profile_key=active_profile,
                    workflow_context=current_workflow_context,
                )

            direct_builtin_tool.__signature__ = _signature_from_json_schema(builtin_tool.input_schema)  # type: ignore[attr-defined]
            mcp.tool(
                name=name,
                description=(
                    f"直连内置工具 {provider.source_key}.{builtin_tool.tool}。"
                    f"{builtin_tool.description}"
                ),
            )(direct_builtin_tool)

    register_direct_builtin_tools()

    def register_pinned_tools() -> None:
        if profile_key is None or not hasattr(service.capabilities, "pinned_tool_specs"):
            return
        registered_names = {"search", "execute", "artifacts_search"}
        for spec in service.capabilities.pinned_tool_specs(_active_actor(), profile_key):
            name = spec["generated_tool_name"]
            if name in registered_names:
                continue
            registered_names.add(name)
            invalid_parameter_names = _invalid_json_schema_property_names(spec.get("input_schema") or {})
            if invalid_parameter_names:
                logger.warning(
                    "跳过 pinned MCP tool name=%s invalid_schema_fields=%s",
                    name,
                    invalid_parameter_names,
                )
                continue

            async def pinned_tool(_spec: dict[str, Any] = spec, **kwargs: Any) -> dict[str, Any]:
                active_profile = _request_profile.get() or profile_key
                return await service.capabilities.execute(
                    actor=_active_actor(),
                    service=_spec["service_key"],
                    tool_name=_spec["tool_name"],
                    params=kwargs,
                    profile_key=active_profile,
                )

            pinned_tool.__signature__ = _signature_from_json_schema(spec.get("input_schema") or {})  # type: ignore[attr-defined]
            mcp.tool(name=name, description=spec["description"])(pinned_tool)

    register_pinned_tools()

    return mcp


def _append_workflow_run_log(
    *,
    service: AgentBridgeService,
    profile_key: str | None,
    workflow_key: str,
    run_id: str,
    task_key: str | None,
    level: str,
    stage: str,
    message: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    service.workflows.require_workflow_run_context(
        profile_key=profile_key,
        workflow_key=workflow_key,
        run_id=run_id,
    )
    service.workflows.append_run_log(
        workflow_key=workflow_key,
        run_id=run_id,
        task_key=task_key,
        level=level,
        stage=stage,
        message=message,
        payload=payload,
    )
    return {"ok": True}


def _search_artifacts_if_enabled(
    bridge_service: AgentBridgeService,
    *,
    actor: str,
    profile_key: str | None,
    query: str,
    path: str,
    limit: int,
) -> dict[str, Any]:
    bridge_service.capabilities.require_top_level_mcp_tool_enabled("artifacts_search")
    return bridge_service.workflows.search_artifacts(
        actor=actor,
        profile_key=profile_key,
        query=query,
        tags=[],
        path=path,
        workflow_key=None,
        include_history=False,
        limit=limit,
        trusted_profile_context=True,
    )


def _workflow_get_task_if_enabled(
    bridge_service: AgentBridgeService,
    profile_key: str | None,
    workflow_key: str,
    run_id: str,
) -> dict[str, Any]:
    bridge_service.capabilities.require_top_level_mcp_tool_enabled("workflow_get_task")
    return bridge_service.workflows.get_task_for_agent(
        profile_key=profile_key, workflow_key=workflow_key, run_id=run_id
    )


def _workflow_set_task_if_enabled(
    bridge_service: AgentBridgeService,
    profile_key: str | None,
    workflow_key: str,
    run_id: str,
    tasks: list[dict[str, Any]],
) -> dict[str, Any]:
    bridge_service.capabilities.require_top_level_mcp_tool_enabled("workflow_set_task")
    return bridge_service.workflows.set_tasks_for_agent(
        profile_key=profile_key, workflow_key=workflow_key, run_id=run_id, tasks=tasks
    )


def _append_workflow_run_log_if_enabled(
    *,
    bridge_service: AgentBridgeService,
    **kwargs: Any,
) -> dict[str, Any]:
    bridge_service.capabilities.require_top_level_mcp_tool_enabled("workflow_run_log")
    return _append_workflow_run_log(**kwargs)


def setup_mcp_route(
    app: Any,
    service: AgentBridgeService,
    identity_resolver: RequestIdentityResolver,
) -> None:
    """Register MCP streamable HTTP endpoint on a FastAPI app."""
    router = APIRouter()

    @router.api_route("/mcp", methods=["POST", "GET", "DELETE"])
    async def handle_mcp(request: Request) -> Response:
        actor = identity_resolver.resolve(request).user_id
        profile = request.headers.get("x-agent-bridge-metamcp-profile")
        workflow_header = request.headers.get("x-agent-bridge-workflow")
        workflow_key = request.headers.get("x-agent-bridge-workflow-key")
        workflow_run_id = request.headers.get("x-agent-bridge-workflow-run-id")
        workflow_context = (
            {"workflow": True, "workflow_key": workflow_key, "run_id": workflow_run_id}
            if workflow_header == "true" and workflow_key and workflow_run_id
            else None
        )
        logger.info(
            "MCP 请求 method=%s actor=%s profile=%s workflow=%s",
            request.method,
            actor,
            profile,
            bool(workflow_context),
        )
        actor_token = _request_actor.set(actor)
        token = _request_profile.set(profile)
        workflow_token = _request_workflow_context.set(workflow_context)
        try:
            mcp = create_mcp_server(service, profile_key=profile, workflow_context=workflow_context)
            response = await _dispatch_mcp(mcp, request)
            logger.info("MCP 响应 status=%d profile=%s", response.status_code, profile)
            return response
        except Exception as exc:
            logger.error("MCP 错误 profile=%s 错误=%s", profile, exc)
            raise
        finally:
            _request_workflow_context.reset(workflow_token)
            _request_profile.reset(token)
            _request_actor.reset(actor_token)

    app.include_router(router)


async def _dispatch_mcp(mcp: FastMCP, request: Request) -> Response:
    response_started = False
    response_status = 200
    response_headers: list[tuple[bytes, bytes]] = []
    response_body = bytearray()

    async def capture_send(message: dict[str, Any]) -> None:
        nonlocal response_started, response_status
        if message["type"] == "http.response.start":
            response_started = True
            response_status = message["status"]
            response_headers.extend(message.get("headers", []))
        elif message["type"] == "http.response.body":
            response_body.extend(message.get("body", b""))

    transport = StreamableHTTPServerTransport(
        mcp_session_id=None,
        is_json_response_enabled=True,
    )

    async with anyio.create_task_group() as tg:

        async def run_server(*, task_status=anyio.TASK_STATUS_IGNORED) -> None:
            async with transport.connect() as (read_stream, write_stream):
                task_status.started()
                await mcp._mcp_server.run(
                    read_stream,
                    write_stream,
                    mcp._mcp_server.create_initialization_options(),
                    stateless=True,
                )

        await tg.start(run_server)
        await transport.handle_request(request.scope, request.receive, capture_send)
        await transport.terminate()
        tg.cancel_scope.cancel()

    if not response_started:
        logger.warning("MCP transport 未产生响应，返回 500")
        return Response(status_code=500, content=b"Transport did not produce a response")

    return Response(
        content=bytes(response_body),
        status_code=response_status,
        headers={k.decode(): v.decode() for k, v in response_headers},
    )
