from __future__ import annotations

from pathlib import Path

import pytest
from claude_agent_sdk import ResultMessage

from agent_bridge.knowledge_management.code_knowledge.ua_client import UA_DIR_LEGACY, UAAvailability, UnderstandAnythingClient


def test_analyze_uses_agent_sdk_options(wm_paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_bridge.agent_runtime.adapters import claude as claude_agent
    from agent_bridge.app.service import AgentBridgeService

    # UA analysis now delegates the SDK loop to AgentService, so the SDK is
    # patched on the Claude adapter module and the client is wired with one.
    agents = AgentBridgeService.create(wm_paths, {"root"}).agents
    client = UnderstandAnythingClient(agent_service=agents)

    project_dir = tmp_path / "repo"
    graph_dir = project_dir / UA_DIR_LEGACY
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

    monkeypatch.setattr(claude_agent, "ClaudeAgentOptions", _FakeOptions)
    monkeypatch.setattr(claude_agent, "claude_query", fake_query)
    monkeypatch.setattr(
        claude_agent, "claude_settings_env", lambda: {"ANTHROPIC_BASE_URL": "https://example.test"}
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


def test_understand_anything_repo_lives_under_agent_bridge_plugins(wm_paths) -> None:
    client = UnderstandAnythingClient(root=wm_paths.root)

    assert client._ua_repo_dir() == wm_paths.root / "plugins" / "understand-anything"


def test_ua_dir_prefers_new_dot_ua_over_legacy(tmp_path: Path) -> None:
    """UA 2.9.0 新默认目录 ``.ua`` 存在时优先读取,否则回退 legacy ``.understand-anything``。"""
    client = UnderstandAnythingClient()

    # 全新项目:无任何 UA 目录 → 回退到 legacy 路径(等 UA 写入后由 skill 决定落到哪)
    assert client._ua_dir(tmp_path) == tmp_path / ".understand-anything"

    # 两个目录都存在时优先新目录(新规范代表最新分析结果)
    (tmp_path / ".ua").mkdir()
    (tmp_path / ".understand-anything").mkdir()
    assert client._ua_dir(tmp_path) == tmp_path / ".ua"

    # 只有 legacy → 维持老项目行为
    only_legacy = tmp_path / "legacy_only"
    (only_legacy / ".understand-anything").mkdir(parents=True)
    assert client._ua_dir(only_legacy) == only_legacy / ".understand-anything"
