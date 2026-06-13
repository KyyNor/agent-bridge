"""Standard MCP endpoint using FastMCP with per-request stateless transport."""

from __future__ import annotations

import inspect
import json
import logging
import time
from contextvars import ContextVar
from typing import Any

import anyio
from fastapi import APIRouter, Request, Response
from mcp.server.fastmcp import FastMCP
from mcp.server.streamable_http import StreamableHTTPServerTransport

from agent_bridge.core.config import default_user
from agent_bridge.knowledge.service import AgentBridgeService

logger = logging.getLogger("agent_bridge.mcp")

_request_profile: ContextVar[str | None] = ContextVar("_request_profile", default=None)


def _annotation_from_json_schema(definition: dict[str, Any]) -> Any:
    value_type = definition.get("type")
    if isinstance(value_type, list):
        value_type = next((item for item in value_type if item != "null"), None)
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


def _signature_from_json_schema(schema: dict[str, Any]) -> inspect.Signature:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    required = schema.get("required")
    required_names = set(required if isinstance(required, list) else [])
    parameters = []
    for name, definition in properties.items():
        if not isinstance(name, str) or not name.isidentifier():
            continue
        if not isinstance(definition, dict):
            definition = {}
        default = inspect._empty if name in required_names else definition.get("default", None)
        parameters.append(
            inspect.Parameter(
                name,
                inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=_annotation_from_json_schema(definition),
            )
        )
    return inspect.Signature(parameters=parameters, return_annotation=dict[str, Any])


def create_mcp_server(service: AgentBridgeService, profile_key: str | None = None) -> FastMCP:
    mcp = FastMCP(
        name="agent-bridge",
        instructions=(
            "Agent Capability Hub gateway. "
            "Use search to discover available MCP tools and services, "
            "then use execute to run them."
        ),
    )

    @mcp.tool(
        description=(
            "Browse and search the Agent Capability Hub registry. "
            "With no arguments, returns visible MCP services. "
            "With path=service_key, returns tools under that service. "
            "query filters the current path."
        ),
    )
    def search(
        path: str | None = None,
        query: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        active_profile = _request_profile.get() or profile_key
        logger.info("搜索 profile=%s path=%s query=%s limit=%s", active_profile, path, query, limit)
        started = time.monotonic()
        try:
            result = service.capabilities.search(
                actor=default_user(),
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
        description="Execute a registered read-only MCP tool through the Agent Capability Hub gateway.",
    )
    async def execute(
        service_key: str,
        tool: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        active_profile = _request_profile.get() or profile_key
        logger.info("执行 profile=%s service=%s tool=%s args=%s", active_profile, service_key, tool, json.dumps(arguments or {}, ensure_ascii=False))
        started = time.monotonic()
        try:
            result = await service.capabilities.execute(
                actor=default_user(),
                service=service_key,
                tool=tool,
                arguments=arguments or {},
                profile_key=active_profile,
            )
            logger.info("执行完成 profile=%s service=%s tool=%s 耗时=%.0fms success=%s", active_profile, service_key, tool, (time.monotonic() - started) * 1000, result.get("success"))
            return result
        except Exception as exc:
            logger.error("执行失败 profile=%s service=%s tool=%s 耗时=%.0fms 错误=%s", active_profile, service_key, tool, (time.monotonic() - started) * 1000, exc)
            raise

    def register_pinned_tools() -> None:
        if profile_key is None or not hasattr(service.capabilities, "pinned_tool_specs"):
            return
        registered_names = {"search", "execute"}
        for spec in service.capabilities.pinned_tool_specs(default_user(), profile_key):
            name = spec["generated_tool_name"]
            if name in registered_names:
                continue
            registered_names.add(name)

            async def pinned_tool(_spec: dict[str, Any] = spec, **kwargs: Any) -> dict[str, Any]:
                active_profile = _request_profile.get() or profile_key
                return await service.capabilities.execute(
                    actor=default_user(),
                    service=_spec["service_key"],
                    tool=_spec["tool_name"],
                    arguments=kwargs,
                    profile_key=active_profile,
                )

            pinned_tool.__signature__ = _signature_from_json_schema(spec.get("input_schema") or {})  # type: ignore[attr-defined]
            mcp.tool(name=name, description=spec["description"])(pinned_tool)

    register_pinned_tools()

    return mcp


def setup_mcp_route(app: Any, service: AgentBridgeService) -> None:
    """Register MCP streamable HTTP endpoint on a FastAPI app."""
    router = APIRouter()

    @router.api_route("/mcp", methods=["POST", "GET", "DELETE"])
    async def handle_mcp(request: Request) -> Response:
        profile = request.headers.get("x-agent-bridge-metamcp-profile")
        logger.info("MCP 请求 method=%s profile=%s", request.method, profile)
        mcp = create_mcp_server(service, profile_key=profile)
        token = _request_profile.set(profile)
        try:
            response = await _dispatch_mcp(mcp, request)
            logger.info("MCP 响应 status=%d profile=%s", response.status_code, profile)
            return response
        except Exception as exc:
            logger.error("MCP 错误 profile=%s 错误=%s", profile, exc)
            raise
        finally:
            _request_profile.reset(token)

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
        return Response(status_code=500, content=b"Transport did not produce a response")

    return Response(
        content=bytes(response_body),
        status_code=response_status,
        headers={k.decode(): v.decode() for k, v in response_headers},
    )
