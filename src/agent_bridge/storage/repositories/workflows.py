"""工作流持久化仓储的聚合入口。

公开 API 由明确职责的 mixin 组成，既有 SQLiteStore 调用方仍使用同一仓储，
无需动态方法转发。
"""

from __future__ import annotations

from .workflow_artifacts import WorkflowArtifactsRepositoryMixin
from .workflow_definitions import WorkflowDefinitionsRepositoryMixin
from .workflow_runs import WorkflowRunsRepositoryMixin
from .workflow_task_imports import WorkflowTaskImportsRepositoryMixin
from .workflow_task_queue import WorkflowTaskQueueRepositoryMixin


class WorkflowsRepository(
    WorkflowDefinitionsRepositoryMixin,
    WorkflowTaskImportsRepositoryMixin,
    WorkflowTaskQueueRepositoryMixin,
    WorkflowRunsRepositoryMixin,
    WorkflowArtifactsRepositoryMixin,
):
    """以同一连接工厂组合全部工作流持久化能力。"""

    def __init__(self, db_path, connect):
        self._db_path = db_path
        self._connect = connect
