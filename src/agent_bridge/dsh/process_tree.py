"""DSH 进程树枚举的兼容入口。

实现位于 :mod:`agent_bridge.agent_workspace.process_tree`（工作台通用组件）。
本模块保留为一层**真实转发**而非静态重导出：既有测试与调用方在
``agent_bridge.dsh.process_tree`` 上 monkeypatch ``descendants`` /
``marked_pids``，转发层让这些替换对实际执行路径同样生效。新代码请直接使用
``agent_bridge.agent_workspace.process_tree``。
"""

from __future__ import annotations

from agent_bridge.agent_workspace import process_tree as _impl
from agent_bridge.agent_workspace.process_tree import (  # noqa: F401
    PI_RUNTIME_MARK_ENV,
    RUNTIME_MARK_ENV,
    runtime_mark_value,
)

# 这些函数在本模块内定义并转发：monkeypatch 本模块属性即可影响实际行为。
descendants = _impl.descendants
marked_pids = _impl.marked_pids
process_parent_map = _impl.process_parent_map
process_is_zombie = _impl.process_is_zombie
discard_own_process = _impl.discard_own_process

__all__ = [
    "PI_RUNTIME_MARK_ENV",
    "RUNTIME_MARK_ENV",
    "descendants",
    "discard_own_process",
    "marked_pids",
    "process_is_zombie",
    "process_parent_map",
    "runtime_mark_value",
]
