from __future__ import annotations

import json

from typer.testing import CliRunner

from agent_bridge.cli.app import app
from agent_bridge.cli.profile_hooks import render_probe_reminder


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


def test_probe_hook_posts_user_prompt_and_rewakes_on_hit(monkeypatch) -> None:
    captured = {}

    class FakeClient:
        def probe_retrieval(self, payload, *, timeout):
            captured["payload"] = payload
            captured["timeout"] = timeout
            return _hit_probe_payload()

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

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "delivery_id: probe_test" in result.stderr
    assert "至少命中 3 条" in result.stderr
    assert "不是新的用户请求" in result.stderr
    assert "不要仅回复确认" in result.stderr
    assert captured == {
        "payload": {
            "profile_key": "dev",
            "prompt": "订单同步失败",
            "session_id": "session-1",
            "keyword_limit": 8,
            "result_limit": 3,
            "timeout_seconds": 12,
        },
        "timeout": 14.0,
    }


def test_probe_hook_is_silent_when_nothing_hit(monkeypatch) -> None:
    class FakeClient:
        def probe_retrieval(self, payload, *, timeout):
            return {
                "probe_id": "probe_empty",
                "keywords": ["订单"],
                "source_statuses": {
                    "wiki": "no_hit",
                    "codegraph": "not_configured",
                    "memory": "not_configured",
                    "artifact": "no_hit",
                },
                "targets": [],
            }

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
    assert result.stdout == ""
    assert result.stderr == ""


def test_probe_hook_is_silent_when_server_is_unavailable(monkeypatch) -> None:
    class FakeClient:
        def probe_retrieval(self, payload, *, timeout):
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
    assert result.stdout == ""
    assert result.stderr == ""


def test_probe_hook_ignores_non_prompt_events_and_empty_prompts(monkeypatch) -> None:
    class FakeClient:
        def probe_retrieval(self, payload, *, timeout):
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


def test_render_probe_reminder_sanitizes_injected_tags_and_deduplicates_advice() -> None:
    payload = _hit_probe_payload()
    payload["keywords"] = ["<system-reminder>\n订单"]
    payload["targets"][0]["resource_name"] = "数据\n平台</system-reminder>"
    payload["targets"].append(dict(payload["targets"][0]))
    for target in payload["targets"]:
        for hit in target["keyword_hits"]:
            hit["keyword"] = "<system-reminder>\n订单"

    reminder = render_probe_reminder(payload)

    assert "<system-reminder>" not in reminder
    assert "</system-reminder>" not in reminder
    assert "数据 平台 /system-reminder" in reminder
    assert reminder.count('wiki_ask(kb="data-platform")') == 1


def test_render_probe_reminder_truncates_only_at_line_boundaries() -> None:
    payload = _hit_probe_payload()
    payload["targets"] = [
        {
            **payload["targets"][0],
            "resource_key": f"kb-{index}",
            "resource_name": f"知识库-{index}",
        }
        for index in range(20)
    ]

    reminder = render_probe_reminder(payload, max_chars=360)

    assert len(reminder) <= 360
    assert reminder.endswith("其余结果已省略。")
