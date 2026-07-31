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
    stale = "stale"
    running = "running"
    completed = "completed"
    failed = "failed"
    abandoned = "abandoned"
    # 同 task_key 出现更新的 task_version 后，未运行的旧版本被取代。
    # 调度器永不领取 superseded，但其产物历史仍保留在 workflow_artifacts 中。
    superseded = "superseded"


class WorkflowArtifactFormat(str, Enum):
    markdown = "markdown"
    html = "html"


class WorkflowType(str, Enum):
    operation = "operation"
    summary = "summary"
