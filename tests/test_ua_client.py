from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_bridge.codegraph.ua_client import UA_DIR, UAAvailability, UnderstandAnythingClient


def test_analyze_uses_auto_permission_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = client.analyze(project_dir)

    assert result.success is True
    assert captured["cmd"][:4] == ["claude", "-p", "--permission-mode", "auto"]
    assert "--dangerously-skip-permissions" not in captured["cmd"]
