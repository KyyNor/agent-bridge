"""通用工作台 Runtime：与具体 Coding Agent 无关的进程托管与能力平面接入。

按 :class:`~agent_bridge.agent_workspace.adapter.WorkspaceAgentAdapter` 组织，
新增一种工作台 Agent 只需实现并注册一个 adapter，不修改通用 runtime 与
中心分发链。
"""

from __future__ import annotations