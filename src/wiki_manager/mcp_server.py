"""MCP server exposing search and ask tools for wiki-manager."""
from __future__ import annotations

from mcp.server import Server
from mcp.types import Tool


def create_mcp_server() -> Server:
    """Create an MCP server with search and ask tool definitions."""
    server = Server("wiki-manager")

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

    return server
