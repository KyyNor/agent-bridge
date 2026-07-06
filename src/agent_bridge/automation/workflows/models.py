from __future__ import annotations

from enum import Enum


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
    html = "html"


class WorkflowType(str, Enum):
    operation = "operation"
    summary = "summary"
