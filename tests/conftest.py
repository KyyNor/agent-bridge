from __future__ import annotations

from pathlib import Path

import pytest

from agent_bridge.core.config import AgentBridgePaths


@pytest.fixture(autouse=True)
def _stub_dsh_dump_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """单元测试不执行真实 ``dsh --dump-config``：生效配置视为空（无用户供应商）。

    需要模拟用户供应商的测试自行覆盖 ``injection.read_entry_config``；真实
    DSH 进程测试在模块导入时保留原函数引用。
    """
    from agent_bridge.dsh import injection

    monkeypatch.setattr(injection, "read_entry_config", lambda **_kwargs: None)


@pytest.fixture
def wm_paths(tmp_path: Path) -> AgentBridgePaths:
    return AgentBridgePaths.from_root(tmp_path / "agent-bridge")
