from __future__ import annotations

from pathlib import Path

import pytest


from agent_bridge.knowledge_management.code_knowledge.ua_client import UA_DIR_LEGACY, UnderstandAnythingClient


@pytest.mark.parametrize("skill_directory", [".agents", ".claude"])
def test_analyze_uses_default_coding_agent(tmp_path: Path, skill_directory: str) -> None:
    from types import SimpleNamespace
    from agent_bridge.agent_runtime.service import AgentRunResult

    captured = {}
    class Agents:
        async def run(self, **kwargs):
            captured.update(kwargs)
            kwargs["on_message"]({"type": "result", "text": "done"})
            return AgentRunResult(ok=True, result="ok")

    project_dir = tmp_path / "repo"
    graph_dir = project_dir / UA_DIR_LEGACY
    graph_dir.mkdir(parents=True)
    (graph_dir / "knowledge-graph.json").write_text('{"nodes": [], "edges": []}', encoding="utf-8")
    (project_dir / skill_directory / "skills" / "understand").mkdir(parents=True)
    agents = Agents()
    agents.coding_agents = SimpleNamespace(get=lambda: object())
    result = UnderstandAnythingClient(agent_service=agents).analyze(project_dir)
    assert result.success is True
    assert "backend_key" not in captured
    assert captured["cwd"] == project_dir
    assert str(project_dir / skill_directory / "skills" / "understand" / "SKILL.md") in captured["system_prompt_append"]
    assert "done" in result.output


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
