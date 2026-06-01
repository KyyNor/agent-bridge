"""MCP server exposing search and ask tools for wiki-manager."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from mcp.server import Server
from mcp.types import Tool

from wiki_manager.config import DEFAULT_ROOT, WikiManagerPaths, load_server_config
from wiki_manager.services import WikiManagerService


def _to_dict(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [_to_dict(item) for item in value]
    return value


def create_mcp_server(
    *,
    service: WikiManagerService | None = None,
    actor: str = "root",
    paths: WikiManagerPaths | None = None,
    admins: set[str] | None = None,
) -> Server:
    """Create an MCP server with search and ask tool definitions."""
    server = Server("wiki-manager")

    def resolve_service() -> WikiManagerService:
        if service is not None:
            return service
        resolved_paths = paths or WikiManagerPaths.from_root(DEFAULT_ROOT)
        resolved_admins = admins if admins is not None else load_server_config(resolved_paths).admins
        return WikiManagerService.create(resolved_paths, resolved_admins)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="search",
                description="Search knowledge base chunks by query.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "kb_slug": {
                            "type": "string",
                            "description": "Knowledge base slug",
                        },
                        "question": {
                            "type": "string",
                            "description": "Search query",
                        },
                        "backend": {
                            "type": "string",
                            "description": "Backend slug (optional)",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Number of results (default 6)",
                        },
                    },
                    "required": ["kb_slug", "question"],
                },
            ),
            Tool(
                name="ask",
                description="Ask a question against a knowledge base and get an answer with references.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "kb_slug": {
                            "type": "string",
                            "description": "Knowledge base slug",
                        },
                        "question": {
                            "type": "string",
                            "description": "Question to ask",
                        },
                        "backend": {
                            "type": "string",
                            "description": "Backend slug (optional)",
                        },
                        "session_id": {
                            "type": "string",
                            "description": "Session ID for multi-turn (optional)",
                        },
                    },
                    "required": ["kb_slug", "question"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        svc = resolve_service()
        if name == "search":
            results = svc.search(
                actor,
                arguments["kb_slug"],
                arguments["question"],
                backend_slug=arguments.get("backend"),
                top_k=int(arguments.get("top_k", 6)),
            )
            return {"results": _to_dict(results)}
        if name == "ask":
            result = svc.ask(
                actor,
                arguments["kb_slug"],
                arguments["question"],
                backend_slug=arguments.get("backend"),
                session_id=arguments.get("session_id"),
            )
            return _to_dict(result)
        raise ValueError(f"unknown tool: {name}")

    return server
