from __future__ import annotations

import json

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
