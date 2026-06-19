from __future__ import annotations

from pathlib import Path

import pytest
from claude_agent_sdk import ResultMessage

from agent_bridge.knowledge_management.code_knowledge.ua_client import UA_DIR, UAAvailability, UnderstandAnythingClient


def test_analyze_uses_agent_sdk_options(wm_paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_bridge.agent_runtime import service as agent_service_module
    from agent_bridge.app.service import AgentBridgeService

    # UA analysis now delegates the SDK loop to AgentService, so the SDK is
    # patched on the agent_service module and the client is wired with one.
    agents = AgentBridgeService.create(wm_paths, {"root"}).agents
    client = UnderstandAnythingClient(agent_service=agents)

    project_dir = tmp_path / "repo"
    graph_dir = project_dir / UA_DIR
    graph_dir.mkdir(parents=True)
    (graph_dir / "knowledge-graph.json").write_text('{"nodes": [], "edges": []}', encoding="utf-8")
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        client,
        "check_availability",
        lambda project_dir: UAAvailability(claude_installed=True, ua_skill_available=True),
    )

    class _FakeOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    async def fake_query(*, prompt, options):
        captured["prompt"] = prompt
        captured["options_object"] = options
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="session_1",
            result="ok",
            total_cost_usd=0.0,
        )

    monkeypatch.setattr(agent_service_module, "ClaudeAgentOptions", _FakeOptions)
    monkeypatch.setattr(agent_service_module, "claude_query", fake_query)
    monkeypatch.setattr(
        agent_service_module, "claude_settings_env", lambda: {"ANTHROPIC_BASE_URL": "https://example.test"}
    )

    result = client.analyze(project_dir)

    assert result.success is True
    assert str(project_dir) in captured["prompt"]
    options = captured["options_object"].kwargs
    assert options["cwd"] == project_dir
    assert options["tools"] == {"type": "preset", "preset": "claude_code"}
    assert options["permission_mode"] == "auto"
    assert options["env"] == {"ANTHROPIC_BASE_URL": "https://example.test"}
    assert options["setting_sources"] == ["user", "project"]
    assert options["skills"] == ["understand"]
    assert options["include_partial_messages"] is True
    assert "session_1" in (result.output or "")
