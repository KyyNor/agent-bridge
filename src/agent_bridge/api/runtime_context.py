from __future__ import annotations

from typing import Any

from fastapi import Request


def profile_from_headers(request: Request) -> str | None:
    value = request.headers.get("x-agent-bridge-metamcp-profile", "").strip()
    return value or None


def workflow_context_from_headers(request: Request) -> dict[str, Any] | None:
    workflow_enabled = request.headers.get("x-agent-bridge-workflow", "").strip().lower() == "true"
    workflow_key = request.headers.get("x-agent-bridge-workflow-key", "").strip()
    run_id = request.headers.get("x-agent-bridge-workflow-run-id", "").strip()
    if not workflow_enabled:
        return None
    return {
        "workflow": True,
        "workflow_key": workflow_key or None,
        "run_id": run_id or None,
    }
