"""MCP server exposing MetaMCP gateway tools for wiki-manager."""
from __future__ import annotations

from typing import Any

from mcp.server import Server
from mcp.types import Tool

from wiki_manager.config import DEFAULT_ROOT, WikiManagerPaths, load_server_config
from wiki_manager.services import WikiManagerService


def create_mcp_server(
    *,
    service: WikiManagerService | None = None,
    actor: str = "root",
    paths: WikiManagerPaths | None = None,
    admins: set[str] | None = None,
) -> Server:
    """Create an MCP server with MetaMCP gateway tool definitions."""
    server = Server("wiki-manager")

    def resolve_service() -> WikiManagerService:
        if service is not None:
            return service
        resolved_paths = paths or WikiManagerPaths.from_root(DEFAULT_ROOT)
        resolved_admins = admins if admins is not None else load_server_config(resolved_paths).admins
        resolved_service = WikiManagerService.create(resolved_paths, resolved_admins)
        resolved_service.store.init_schema()
        return resolved_service

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="search",
                description=(
                    "Browse and search the Agent Capability Hub registry. "
                    "With no arguments, returns visible MCP services. "
                    "With path=service_key, returns tools under that service. "
                    "query filters the current path."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Registry path. Empty or '/' lists services; "
                                "a service key lists tools under that service."
                            ),
                        },
                        "query": {
                            "type": "string",
                            "description": "Optional natural language filter for services or tools under the selected path.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of items to return. Default 20.",
                        },
                    },
                },
            ),
            Tool(
                name="execute",
                description="Execute a registered read-only MCP tool through the Agent Capability Hub gateway.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "service": {
                            "type": "string",
                            "description": "Registered service key",
                        },
                        "tool": {
                            "type": "string",
                            "description": "Registered tool name",
                        },
                        "arguments": {
                            "type": "object",
                            "description": "Tool arguments",
                        },
                    },
                    "required": ["service", "tool", "arguments"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        svc = resolve_service()
        arguments = arguments or {}
        if name == "search":
            return svc.capabilities.search(
                actor=actor,
                path=arguments.get("path"),
                query=arguments.get("query"),
                limit=int(arguments.get("limit", 20)),
            )
        if name == "execute":
            return await svc.capabilities.execute(
                actor=actor,
                service=arguments["service"],
                tool=arguments["tool"],
                arguments=arguments.get("arguments") or {},
            )
        raise ValueError(f"unknown tool: {name}")

    return server
