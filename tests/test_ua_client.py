from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_bridge.codegraph.ua_client import UA_DIR, UAAvailability, UnderstandAnythingClient


def test_analyze_uses_agent_sdk_options(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_bridge.codegraph import ua_client as ua_module

    client = UnderstandAnythingClient()
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

    class FakeOptions:
        def __init__(self, **kwargs):
            captured["options"] = kwargs

    async def fake_query(*, prompt, options):
        captured["prompt"] = prompt
        captured["options_object"] = options
        yield SimpleNamespace(subtype="success", result="ok", session_id="session_1")

    monkeypatch.setattr(ua_module, "ClaudeAgentOptions", FakeOptions)
    monkeypatch.setattr(ua_module, "claude_query", fake_query)
    monkeypatch.setattr(ua_module, "claude_settings_env", lambda: {"ANTHROPIC_BASE_URL": "https://example.test"})

    result = client.analyze(project_dir)

    assert result.success is True
    assert str(project_dir) in captured["prompt"]
    options = captured["options"]
    assert options["cwd"] == project_dir
    assert options["tools"] == {"type": "preset", "preset": "claude_code"}
    assert options["permission_mode"] == "bypassPermissions"
    assert options["env"] == {"ANTHROPIC_BASE_URL": "https://example.test"}
    assert options["setting_sources"] == ["user", "project"]
    assert options["skills"] == ["understand"]
    assert options["include_partial_messages"] is True
    assert "session_1" in (result.output or "")
