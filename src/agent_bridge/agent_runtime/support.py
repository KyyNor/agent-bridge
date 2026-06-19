"""Shared helpers for Claude Agent SDK runs (MCP config + file staging).

These are extracted from ``workflows/runner.py`` so both the workflow runner
and ``AgentService`` build the Agent Bridge MCP configuration the same way.
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

    Returns an empty mapping (no MCP access) when ``profile`` is falsy. When a
    profile is set, the governance profile header is always included, and the
    workflow headers are added when both ``workflow_key`` and ``run_id`` are
    provided — the ``/mcp`` endpoint consumes these to scope the tool set.
    """
    if not profile:
        return {}
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


def write_run_mcp_json(path: Path, config: dict[str, Any]) -> None:
    """Write an MCP server config to ``path`` as JSON (creates parent dirs)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
