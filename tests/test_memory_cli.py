from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agent_bridge.cli.app import app


runner = CliRunner()


def test_memory_hook_posts_stdin_payload_to_server(monkeypatch):
    captured = {}

    class FakeClient:
        def post_memory_hook(self, action, payload, *, timeout):
            captured["action"] = action
            captured["payload"] = payload
            captured["timeout"] = timeout
            return {"stdout": '{"continue":true}', "stderr": "", "exit_code": 0, "status": "ok"}

    monkeypatch.setattr("agent_bridge.cli.memory.AgentBridgeClient", lambda base_url, linux_user: FakeClient())

    result = runner.invoke(
        app,
        [
            "memory",
            "hook",
            "claude-code",
            "observation",
            "--profile",
            "dev",
            "--server-url",
            "http://bridge.example",
            "--event",
            "PostToolUse",
            "--matcher",
            "*",
            "--timeout",
            "120",
        ],
        input=json.dumps({"tool_name": "Read"}),
    )

    assert result.exit_code == 0
    assert result.stdout == '{"continue":true}\n'
    assert captured == {
        "action": "observation",
        "payload": {
            "profile_key": "dev",
            "event_name": "PostToolUse",
            "matcher": "*",
            "payload": {"tool_name": "Read"},
            "hook_timeout_seconds": 120,
            "source": "claude-code",
        },
        "timeout": 125.0,
    }


def test_memory_hook_noops_when_server_unreachable(monkeypatch):
    class FakeClient:
        def post_memory_hook(self, action, payload, *, timeout):
            raise RuntimeError("connection refused")

    monkeypatch.setattr("agent_bridge.cli.memory.AgentBridgeClient", lambda base_url, linux_user: FakeClient())

    result = runner.invoke(
        app,
        ["memory", "hook", "claude-code", "context", "--profile", "dev", "--server-url", "http://bridge.example"],
        input="{}",
    )

    assert result.exit_code == 0
    assert result.stdout == '{"continue":true,"suppressOutput":true}\n'


def test_user_scope_memory_hook_exits_silently_when_project_hook_exists(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    settings_path = tmp_path / ".claude" / "settings.local.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": (
                                        "agent-bridge memory hook claude-code summarize "
                                        "--profile project --agent-bridge-hook-id agent-bridge-memory"
                                    ),
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    class FakeClient:
        def post_memory_hook(self, action, payload, *, timeout):
            raise AssertionError("suppressed user hook should not call Agent Bridge")

    monkeypatch.setattr("agent_bridge.cli.memory.AgentBridgeClient", lambda base_url, linux_user: FakeClient())

    result = runner.invoke(
        app,
        [
            "memory",
            "hook",
            "claude-code",
            "summarize",
            "--profile",
            "user",
            "--server-url",
            "http://bridge.example",
            "--event",
            "Stop",
            "--scope",
            "user",
        ],
        input=json.dumps({"session_id": "abc123", "cwd": str(tmp_path), "hook_event_name": "Stop"}),
    )

    assert result.exit_code == 0
    assert result.stdout == ""


def test_user_scope_memory_hook_ignores_home_settings_when_checking_project(monkeypatch, tmp_path: Path):
    home = tmp_path / "home"
    project = tmp_path / "project"
    home_settings = home / ".claude" / "settings.json"
    home_settings.parent.mkdir(parents=True)
    project.mkdir()
    home_settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": (
                                        "agent-bridge memory hook claude-code summarize "
                                        "--profile user --agent-bridge-hook-id agent-bridge-memory"
                                    ),
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(project)
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    captured = {}

    class FakeClient:
        def post_memory_hook(self, action, payload, *, timeout):
            captured["action"] = action
            return {"stdout": "", "stderr": "", "exit_code": 0, "status": "ok"}

    monkeypatch.setattr("agent_bridge.cli.memory.AgentBridgeClient", lambda base_url, linux_user: FakeClient())

    result = runner.invoke(
        app,
        [
            "memory",
            "hook",
            "claude-code",
            "summarize",
            "--profile",
            "user",
            "--server-url",
            "http://bridge.example",
            "--event",
            "Stop",
            "--scope",
            "user",
        ],
        input=json.dumps({"session_id": "abc123", "cwd": str(project), "hook_event_name": "Stop"}),
    )

    assert result.exit_code == 0
    assert captured == {"action": "summarize"}
