"""工作台 Agent adapter 注册表。

新增一种工作台 Agent 只需在此注册实现，通用 runtime、代理与 API 路由按
``agent`` 参数分发，不写中心 if/elif（与能力中心的 ``CapabilitySourceAdapter``
规范一致）。
"""

from __future__ import annotations

from agent_bridge.agent_workspace.adapters.dsh import DshWorkspaceAdapter
from agent_bridge.agent_workspace.adapters.pi import PiWorkspaceAdapter
from agent_bridge.agent_workspace.scopes import (
    WORKSPACE_AGENT_DSH,
    WORKSPACE_AGENT_PI,
)

# agent slug → adapter 实现类。
ADAPTERS: dict[str, type] = {
    WORKSPACE_AGENT_DSH: DshWorkspaceAdapter,
    WORKSPACE_AGENT_PI: PiWorkspaceAdapter,
}

__all__ = ["ADAPTERS", "DshWorkspaceAdapter", "PiWorkspaceAdapter"]
