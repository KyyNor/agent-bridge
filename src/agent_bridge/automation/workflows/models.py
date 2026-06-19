from __future__ import annotations

from enum import Enum
from typing import Any


class WorkflowStatus(str, Enum):
    active = "active"
    disabled = "disabled"


class WorkflowRunStatus(str, Enum):
    running = "running"
    completed = "completed"
    no_task = "no_task"
    failed = "failed"
    stopped = "stopped"


class WorkflowTaskStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    abandoned = "abandoned"


class WorkflowArtifactFormat(str, Enum):
    markdown = "markdown"


def require_manifest(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("manifest must be an object")
    for key in ("name", "nodes", "edges", "schemas"):
        if key not in value:
            raise ValueError(f"manifest missing required key: {key}")
    if not isinstance(value["nodes"], list):
        raise ValueError("manifest.nodes must be a list")
    if not isinstance(value["edges"], list):
        raise ValueError("manifest.edges must be a list")
    if not isinstance(value["schemas"], dict):
        raise ValueError("manifest.schemas must be an object")
    return value
