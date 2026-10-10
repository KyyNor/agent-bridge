"""工作空间范围（scope）与代理前缀。

范围语义对所有工作台 Agent 一致：

- ``personal``：runtime 身份 = 业务用户，配置目录按业务用户隔离；
- ``shared``：runtime 身份 = Linux 用户，同组成员共享同一实例与配置目录。

代理前缀按 Agent 区分（DSH 的 ``/agent-workspace``、Pi 的
``/agent-workspace-pi``），前缀是代理判定 (agent, scope) 的唯一依据。
"""

from __future__ import annotations

from agent_bridge.core.domain import ValidationError

WORKSPACE_SCOPE_PERSONAL = "personal"
WORKSPACE_SCOPE_SHARED = "shared"
WORKSPACE_SCOPES = (WORKSPACE_SCOPE_PERSONAL, WORKSPACE_SCOPE_SHARED)
DEFAULT_WORKSPACE_SCOPE = WORKSPACE_SCOPE_PERSONAL


def normalize_workspace_scope(value: object) -> str:
    """校验并归一化工作空间范围；非法值明确报错。"""
    scope = str(value or "").strip() or DEFAULT_WORKSPACE_SCOPE
    if scope not in WORKSPACE_SCOPES:
        raise ValidationError(f"工作空间范围不合法：{value!r}（只支持 personal/shared）")
    return scope


# 工作台 Agent 标识：API 的 ``agent=`` 参数、状态与配置分组都用它。
WORKSPACE_AGENT_DSH = "dsh"
WORKSPACE_AGENT_PI = "pi"
WORKSPACE_AGENTS = (WORKSPACE_AGENT_DSH, WORKSPACE_AGENT_PI)
DEFAULT_WORKSPACE_AGENT = WORKSPACE_AGENT_DSH


def normalize_workspace_agent(value: object) -> str:
    """校验并归一化工作台 Agent 标识；非法值明确报错。"""
    agent = str(value or "").strip() or DEFAULT_WORKSPACE_AGENT
    if agent not in WORKSPACE_AGENTS:
        raise ValidationError(f"工作台 Agent 不合法：{value!r}（只支持 dsh/pi）")
    return agent