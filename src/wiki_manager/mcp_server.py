"""Standard MCP endpoint using FastMCP with per-request stateless transport."""

from __future__ import annotations

import json
import logging
import time
from contextvars import ContextVar
from typing import Any

import anyio
from fastapi import APIRouter, Request, Response
from mcp.server.fastmcp import FastMCP
from mcp.server.streamable_http import StreamableHTTPServerTransport

from wiki_manager.config import default_user
from wiki_manager.services import WikiManagerService

logger = logging.getLogger("wiki_manager.mcp")

_request_profile: ContextVar[str | None] = ContextVar("_request_profile", default=None)


def create_mcp_server(service: WikiManagerService) -> FastMCP:
    mcp = FastMCP(
        name="wiki-manager",
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
        profile_key = _request_profile.get()
        logger.info("search profile=%s path=%s query=%s limit=%s", profile_key, path, query, limit)
        started = time.monotonic()
        try:
            result = service.capabilities.search(
                actor=default_user(),
                path=path,
                query=query,
                limit=limit,
                profile_key=profile_key,
            )
            logger.info("search ok profile=%s duration=%.0fms items=%d", profile_key, (time.monotonic() - started) * 1000, len(result.get("items", [])))
            return result
        except Exception as exc:
            logger.error("search fail profile=%s duration=%.0fms error=%s", profile_key, (time.monotonic() - started) * 1000, exc)
            raise

    @mcp.tool(
        description="Execute a registered read-only MCP tool through the Agent Capability Hub gateway.",
    )
    async def execute(
        service_key: str,
        tool: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        profile_key = _request_profile.get()
        logger.info("execute profile=%s service=%s tool=%s args=%s", profile_key, service_key, tool, json.dumps(arguments or {}, ensure_ascii=False))
        started = time.monotonic()
        try:
            result = await service.capabilities.execute(
                actor=default_user(),
                service=service_key,
                tool=tool,
                arguments=arguments or {},
                profile_key=profile_key,
            )
            logger.info("execute ok profile=%s service=%s tool=%s duration=%.0fms success=%s", profile_key, service_key, tool, (time.monotonic() - started) * 1000, result.get("success"))
            return result
        except Exception as exc:
            logger.error("execute fail profile=%s service=%s tool=%s duration=%.0fms error=%s", profile_key, service_key, tool, (time.monotonic() - started) * 1000, exc)
            raise

    return mcp


def setup_mcp_route(app: Any, service: WikiManagerService) -> None:
    """Register MCP streamable HTTP endpoint on a FastAPI app."""
    mcp = create_mcp_server(service)
    router = APIRouter()

    @router.api_route("/mcp", methods=["POST", "GET", "DELETE"])
    async def handle_mcp(request: Request) -> Response:
        profile = request.headers.get("x-wiki-metamcp-profile")
        logger.info("MCP request method=%s profile=%s", request.method, profile)
        token = _request_profile.set(profile)
        try:
            response = await _dispatch_mcp(mcp, request)
            logger.info("MCP response status=%d profile=%s", response.status_code, profile)
            return response
        except Exception as exc:
            logger.error("MCP error profile=%s error=%s", profile, exc)
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
