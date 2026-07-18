"""Shared helpers for Claude Agent SDK runs (MCP config + file staging).

Workflow DAG nodes and ``AgentService`` build the Agent Bridge MCP
configuration through these shared helpers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def build_agent_bridge_server_config(
    url: str,
    profile: str | None = None,
    *,
    workflow_key: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Build the ``.mcp.json`` ``mcpServers`` mapping for an Agent Bridge run.

    Returns an empty ``mcpServers`` record (no MCP access) when ``profile`` is
    falsy. When a profile is set, the governance profile header is always
    included, and the workflow headers are added when both ``workflow_key`` and
    ``run_id`` are provided — the ``/mcp`` endpoint consumes these to scope the
    tool set.
    """
    if not profile:
        return {"mcpServers": {}}
    headers: dict[str, str] = {"X-Agent-Bridge-MetaMCP-Profile": profile}
    if workflow_key and run_id:
        headers["X-Agent-Bridge-Workflow"] = "true"
        headers["X-Agent-Bridge-Workflow-Key"] = workflow_key
        headers["X-Agent-Bridge-Workflow-Run-Id"] = run_id
    return {
        "mcpServers": {
            "agent-bridge": {
                "type": "http",
                "url": url,
                "headers": headers,
            }
        }
    }


def build_opencode_mcp_config(config: dict[str, Any]) -> dict[str, Any]:
    """Convert Claude-style ``mcpServers`` config to OpenCode's ``mcp`` shape."""
    if "mcp" in config and "mcpServers" not in config:
        return config

    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        return {"mcp": {}}

    converted: dict[str, Any] = {}
    for name, raw_server in servers.items():
        if not isinstance(raw_server, dict):
            continue
        server = dict(raw_server)
        server_type = server.pop("type", None)
        if server_type in {"http", "sse", "remote"}:
            server["type"] = "remote"
        else:
            server["type"] = "local"
            command = server.get("command")
            if isinstance(command, str):
                args = server.pop("args", [])
                server["command"] = [command, *(args if isinstance(args, list) else [])]
            if "env" in server and "environment" not in server:
                server["environment"] = server.pop("env")
        converted[str(name)] = server
    return {"mcp": converted}


def write_run_mcp_json(path: Path, config: dict[str, Any]) -> None:
    """Write an MCP server config to ``path`` as JSON (creates parent dirs)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
