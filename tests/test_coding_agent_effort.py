"""Coding Agent 思考力度（effort）配置的装配与校验。"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_bridge.agent_runtime.adapters import (
    CodexCodingAgent,
    OpenCodeCodingAgent,
    PiCodingAgent,
)
from agent_bridge.agent_runtime.registry import create_coding_agent_registry
from agent_bridge.agent_runtime.types import CodingAgentRequest
from agent_bridge.core.config import AgentBackendConfig, AgentRuntimeConfig


def _request(tmp_path: Path) -> CodingAgentRequest:
    return CodingAgentRequest(
        prompt="ping",
        cwd=tmp_path,
        mcp_servers={},
        setting_sources=[],
    )


def test_registry_applies_effort_to_all_agent_types(tmp_path: Path):
    registry = create_coding_agent_registry(
        AgentRuntimeConfig(
            default_backend="codex",
            backends=(
                AgentBackendConfig(slug="opencode", agent_type="opencode", effort="high"),
                AgentBackendConfig(slug="codex", agent_type="codex", effort="medium"),
                AgentBackendConfig(slug="pi", agent_type="pi", effort="low"),
            ),
        )
    )

    assert registry.get("opencode").variant == "high"
    assert registry.get("codex").effort == "medium"
    assert registry.get("pi").thinking == "low"


def test_registry_rejects_effort_outside_supported_values():
    with pytest.raises(ValueError, match="不支持配置思考力度"):
        create_coding_agent_registry(
            AgentRuntimeConfig(
                backends=(AgentBackendConfig(slug="dsh", agent_type="dsh", effort="bogus"),),
            )
        )


def test_registry_allows_provider_specific_opencode_variant():
    # OpenCode 的 variant 名由 provider 决定，不做枚举校验、原样透传。
    registry = create_coding_agent_registry(
        AgentRuntimeConfig(
            backends=(AgentBackendConfig(slug="opencode", agent_type="opencode", effort="turbo"),),
        )
    )

    assert registry.get("opencode").variant == "turbo"


def test_supported_efforts_match_cli_vocabularies():
    assert CodexCodingAgent.supported_efforts == frozenset({"minimal", "low", "medium", "high", "xhigh"})
    assert PiCodingAgent.supported_efforts == frozenset({"off", "minimal", "low", "medium", "high", "xhigh"})
    assert OpenCodeCodingAgent.supported_efforts is None
