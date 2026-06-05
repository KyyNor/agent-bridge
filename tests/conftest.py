from __future__ import annotations

from pathlib import Path

import pytest

from agent_bridge.config import AgentBridgePaths


@pytest.fixture
def wm_paths(tmp_path: Path) -> AgentBridgePaths:
    return AgentBridgePaths.from_root(tmp_path / "agent-bridge")
