from __future__ import annotations

import json

from typer.testing import CliRunner

from agent_bridge.cli.app import app
from agent_bridge.knowledge_management.memory.models import NOOP_HOOK_STDOUT


runner = CliRunner()


def _hit_probe_payload() -> dict:
    return {
        "probe_id": "probe_test",
        "profile_key": "dev",
        "session_id": "session-1",
        "keywords": ["订单", "同步"],
        "source_statuses": {
            "wiki": "hit",
            "codegraph": "no_hit",
            "memory": "not_configured",
            "artifact": "hit",
        },
        "targets": [
            {
                "source_type": "wiki",
                "resource_key": "data-platform",
                "resource_name": "数据平台",
                "suggested_tool": "wiki_ask",
                "status": "hit",
                "unique_hit_count": 3,
                "keyword_hits": [
                    {
                        "keyword": "订单",
                        "status": "hit",
                        "count": 3,
                        "capped": True,
                        "duration_ms": 12,
                        "error_type": None,
                    },
                    {
                        "keyword": "同步",
                        "status": "no_hit",
                        "count": 0,
                        "capped": False,
                        "duration_ms": 8,
                        "error_type": None,
                    },
                ],
            },
            {
                "source_type": "artifact",
                "resource_key": "dev",
                "resource_name": "工作流产出物",
                "suggested_tool": "artifacts_search",
                "status": "hit",
                "unique_hit_count": 1,
                "keyword_hits": [
                    {
                        "keyword": "订单",
                        "status": "hit",
                        "count": 1,
                        "capped": False,
                        "duration_ms": 2,
                        "error_type": None,
                    }
                ],
            },
        ],
        "duration_ms": 20,
    }


def test_probe_hook_forwards_raw_payload_and_server_stdout(monkeypatch) -> None:
    captured = {}
    hook_result = {
        "stdout": '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"server reminder"}}',
        "stderr": "",
        "exit_code": 0,
        "status": "ok",
    }

    class FakeClient:
        def post_retrieval_probe_hook(self, payload, *, timeout):
            captured["payload"] = payload
            captured["timeout"] = timeout
            return hook_result

    monkeypatch.setattr(
        "agent_bridge.cli.profile_hooks.AgentBridgeClient",
        lambda base_url, linux_user: FakeClient(),
    )

    result = runner.invoke(
        app,
        [
            "profile",
            "hook",
            "claude-code",
            "retrieval-probe",
            "--profile",
            "dev",
            "--server-url",
            "http://bridge.example",
            "--timeout",
            "12",
            "--agent-bridge-hook-id",
            "agent-bridge-retrieval-probe",
        ],
        input=json.dumps(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "session-1",
                "cwd": "/repo",
                "prompt": "订单同步失败",
            }
        ),
    )

    assert result.exit_code == 0
    assert result.stderr == ""
    assert result.stdout == hook_result["stdout"] + "\n"
    assert captured == {
        "payload": {
            "profile_key": "dev",
            "event_name": "UserPromptSubmit",
            "matcher": None,
            "payload": {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "session-1",
                "cwd": "/repo",
                "prompt": "订单同步失败",
            },
            "hook_timeout_seconds": 12,
        },
        "timeout": 22.0,
    }


def test_probe_hook_is_silent_when_nothing_hit(monkeypatch) -> None:
    class FakeClient:
        def post_retrieval_probe_hook(self, payload, *, timeout):
            return {"stdout": NOOP_HOOK_STDOUT, "stderr": "", "exit_code": 0, "status": "ok"}

    monkeypatch.setattr(
        "agent_bridge.cli.profile_hooks.AgentBridgeClient",
        lambda base_url, linux_user: FakeClient(),
    )

    result = runner.invoke(
        app,
        [
            "profile",
            "hook",
            "claude-code",
            "retrieval-probe",
            "--profile",
            "dev",
        ],
        input=json.dumps(
            {
                "hook_event_name": "UserPromptSubmit",
                "prompt": "订单",
            }
        ),
    )

    assert result.exit_code == 0
    assert result.stdout == NOOP_HOOK_STDOUT + "\n"
    assert result.stderr == ""


def test_probe_hook_is_silent_when_server_is_unavailable(monkeypatch) -> None:
    class FakeClient:
        def post_retrieval_probe_hook(self, payload, *, timeout):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(
        "agent_bridge.cli.profile_hooks.AgentBridgeClient",
        lambda base_url, linux_user: FakeClient(),
    )

    result = runner.invoke(
        app,
        [
            "profile",
            "hook",
            "claude-code",
            "retrieval-probe",
            "--profile",
            "dev",
        ],
        input=json.dumps(
            {
                "hook_event_name": "UserPromptSubmit",
                "prompt": "订单",
            }
        ),
    )

    assert result.exit_code == 0
    assert result.stdout == NOOP_HOOK_STDOUT + "\n"
    assert result.stderr == ""


def test_probe_hook_ignores_non_prompt_events_and_empty_prompts(monkeypatch) -> None:
    class FakeClient:
        def post_retrieval_probe_hook(self, payload, *, timeout):
            raise AssertionError("ignored hook payload must not call server")

    monkeypatch.setattr(
        "agent_bridge.cli.profile_hooks.AgentBridgeClient",
        lambda base_url, linux_user: FakeClient(),
    )

    wrong_event = runner.invoke(
        app,
        [
            "profile",
            "hook",
            "claude-code",
            "retrieval-probe",
            "--profile",
            "dev",
        ],
        input=json.dumps({"hook_event_name": "Stop", "prompt": "订单"}),
    )
    empty_prompt = runner.invoke(
        app,
        [
            "profile",
            "hook",
            "claude-code",
            "retrieval-probe",
            "--profile",
            "dev",
        ],
        input=json.dumps(
            {"hook_event_name": "UserPromptSubmit", "prompt": "  "}
        ),
    )

    assert wrong_event.exit_code == 0
    assert empty_prompt.exit_code == 0
