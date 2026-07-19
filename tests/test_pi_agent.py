from __future__ import annotations

import asyncio
from pathlib import Path

from agent_bridge.agent_runtime.adapters.pi import (
    PiCodingAgent,
    _build_command,
    _events_from_pi_row,
    _extract_json,
    _resolve_model,
)
from agent_bridge.agent_runtime.registry import create_coding_agent_registry
from agent_bridge.agent_runtime.types import CodingAgentRequest
from agent_bridge.core.config import AgentBackendConfig, AgentRuntimeConfig


def test_pi_build_command_minimal_with_bare_model() -> None:
    args = _build_command(
        command="pi",
        prompt="hello",
        model="glm-5.1",
        provider=None,
        thinking=None,
    )

    assert args == [
        "pi",
        "--mode",
        "json",
        "-p",
        "--no-session",
        "--model",
        "glm-5.1",
        "hello",
    ]


def test_pi_build_command_emits_provider_and_model() -> None:
    args = _build_command(
        command="pi",
        prompt="hello",
        model="glm-5.1",
        provider="zai",
        thinking="low",
    )

    assert args == [
        "pi",
        "--mode",
        "json",
        "-p",
        "--no-session",
        "--provider",
        "zai",
        "--model",
        "glm-5.1",
        "--thinking",
        "low",
        "hello",
    ]


def test_pi_resolve_model_forwards_provider_slash_id_verbatim() -> None:
    # pi understands "provider/id" natively, so we hand it the full spec rather
    # than also emitting a redundant --provider.
    assert _resolve_model(provider=None, model="zai/glm-5.1") == (None, "zai/glm-5.1")
    assert _resolve_model(provider="zai", model="glm-5.1") == ("zai", "glm-5.1")
    assert _resolve_model(provider="zai", model=None) == ("zai", None)
    assert _resolve_model(provider=None, model=None) == (None, None)


def test_pi_events_session_header_yields_nothing() -> None:
    row = {"type": "session", "version": 3, "id": "019f75fe-abc", "cwd": "/tmp"}

    events, text, final = _events_from_pi_row(row)

    assert events == []
    assert text is None
    assert final is None


def test_pi_events_message_update_text_delta_surfaces_fragment() -> None:
    # Real shape captured from `pi --mode json` against zai/glm-5.1.
    row = {
        "type": "message_update",
        "assistantMessageEvent": {
            "type": "text_delta",
            "contentIndex": 1,
            "delta": "PONG",
        },
        "message": {
            "role": "assistant",
            "content": [{"type": "thinking", "thinking": "..."}, {"type": "text", "text": "PONG"}],
        },
    }

    events, text, final = _events_from_pi_row(row)

    assert text == "PONG"
    assert final is None
    assert len(events) == 1
    assert events[0]["kind"] == "agent_message"
    assert events[0]["agent_name"] == "pi"
    assert events[0]["source"] == "pi_cli"
    assert events[0]["message"] == "PONG"
    assert events[0]["agent_role"] == "main"


def test_pi_events_message_update_text_end_prefers_event_content() -> None:
    # text_end carries the finalized block content; prefer it over the partial
    # message so callers see the complete segment.
    row = {
        "type": "message_update",
        "assistantMessageEvent": {"type": "text_end", "contentIndex": 1, "content": "done text"},
        "message": {"role": "assistant", "content": [{"type": "text", "text": "partial"}]},
    }

    events, text, final = _events_from_pi_row(row)

    assert text == "done text"
    assert events[0]["message"] == "done text"


def test_pi_events_message_update_thinking_delta_is_dropped() -> None:
    # Thinking deltas are intentionally not surfaced (matches codex/opencode).
    row = {
        "type": "message_update",
        "assistantMessageEvent": {"type": "thinking_delta", "contentIndex": 0, "delta": "internal"},
        "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "internal"}]},
    }

    events, text, final = _events_from_pi_row(row)

    assert events == []
    assert text is None
    assert final is None


def test_pi_events_tool_execution_end_emits_only_result() -> None:
    # tool_execution_end closes a tool call; the matching tool_call was already
    # emitted by tool_execution_start, so end only produces the result event.
    row = {
        "type": "tool_execution_end",
        "toolCallId": "call_abc",
        "toolName": "bash",
        "result": {"content": [{"type": "text", "text": "total 0"}]},
        "isError": False,
    }

    events, text, final = _events_from_pi_row(row)

    assert text is None
    assert final is None
    assert [event["kind"] for event in events] == ["tool_result"]
    assert events[0]["tool_name"] == "bash"
    assert events[0]["tool_use_id"] == "call_abc"
    assert events[0]["status"] == "success"


def test_pi_events_tool_execution_end_marks_failure() -> None:
    row = {
        "type": "tool_execution_end",
        "toolCallId": "call_abc",
        "toolName": "bash",
        "result": {"content": [{"type": "text", "text": "boom"}]},
        "isError": True,
    }

    events, _text, _final = _events_from_pi_row(row)

    assert events[0]["kind"] == "tool_result"
    assert events[0]["status"] == "failed"


def test_pi_events_tool_execution_start_only_emits_call() -> None:
    row = {
        "type": "tool_execution_start",
        "toolCallId": "call_def",
        "toolName": "read",
        "args": {"path": "src/main.py"},
    }

    events, _text, _final = _events_from_pi_row(row)

    assert [event["kind"] for event in events] == ["tool_call"]
    assert events[0]["status"] == "started"


def test_pi_events_tool_execution_update_is_suppressed() -> None:
    # Mid-tool streaming progress would otherwise duplicate "started" per frame;
    # suppress it to match the other CLI adapters.
    row = {
        "type": "tool_execution_update",
        "toolCallId": "call_def",
        "toolName": "bash",
        "partialResult": {"content": [{"type": "text", "text": "partial..."}]},
    }

    events, text, final = _events_from_pi_row(row)

    assert events == []
    assert text is None
    assert final is None


def test_pi_events_turn_end_harvests_cost_and_model() -> None:
    row = {
        "type": "turn_end",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "PONG"}],
            "usage": {
                "input": 13195,
                "output": 22,
                "cost": {"input": 0.0, "output": 0.0, "total": 0.00105},
            },
            "model": "glm-5.1",
        },
        "toolResults": [],
    }

    events, text, final = _events_from_pi_row(row)

    assert text == "PONG"
    assert final is not None
    assert final.cost_usd == 0.00105
    assert final.num_turns == 1
    assert final.model == "glm-5.1"
    assert events[0]["kind"] == "status"
    assert events[0]["total_cost_usd"] == 0.00105


def test_pi_events_agent_end_is_authoritative_final() -> None:
    # agent_end carries the full transcript; we sum cost across assistant turns
    # and take the last assistant text as the result.
    row = {
        "type": "agent_end",
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "hi"}]},
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "first"}],
                "usage": {"cost": {"total": 0.001}},
                "model": "glm-5.1",
            },
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "second answer"}],
                "usage": {"cost": {"total": 0.002}},
                "model": "glm-5.1",
            },
        ],
    }

    events, text, final = _events_from_pi_row(row)

    assert text is None
    assert final is not None
    # Last assistant text wins.
    assert final.result == "second answer"
    # Cost summed across both assistant turns.
    assert final.cost_usd == 0.003
    assert final.num_turns == 2
    assert final.model == "glm-5.1"
    assert events[0]["kind"] == "result"
    assert events[0]["status"] == "success"
    assert events[0]["total_cost_usd"] == 0.003
    assert events[0]["num_turns"] == 2


def test_pi_events_error_row_marks_failed_final() -> None:
    row = {"type": "error", "error": "boom"}

    events, _text, final = _events_from_pi_row(row)

    assert final is not None
    assert final.is_error is True
    assert final.result == "boom"
    assert events[0]["kind"] == "error"
    assert events[0]["status"] == "failed"


def test_pi_events_fallback_surfaces_unrecognized_text() -> None:
    row = {"type": "something_else", "text": "stray content"}

    events, text, _final = _events_from_pi_row(row)

    assert text == "stray content"
    assert events[0]["kind"] == "agent_message"
    assert events[0]["message"] == "stray content"


def test_pi_extract_json_recovers_object_from_surrounding_text() -> None:
    # pi has no --output-schema; structured runs rely on prompt + extraction.
    assert _extract_json('here you go {"a": 1, "b": [2, 3]} trailing') == {"a": 1, "b": [2, 3]}
    assert _extract_json("no json here") is None
    assert _extract_json("") is None


def test_pi_run_consumes_streamed_output(monkeypatch, tmp_path: Path) -> None:
    # End-to-end run loop with a mocked subprocess: feed the real observed
    # event sequence (session → text deltas → turn_end → agent_end) and assert
    # the adapter produces a non-error final with the assistant's answer.
    captured: dict[str, object] = {}

    script_lines = [
        b'{"type":"session","version":3,"id":"sess-1","cwd":"/tmp"}\n',
        b'{"type":"agent_start"}\n',
        b'{"type":"message_update","assistantMessageEvent":{"type":"text_delta","contentIndex":0,"delta":"PONG"},"message":{"role":"assistant","content":[{"type":"text","text":"PONG"}]}}\n',
        b'{"type":"turn_end","message":{"role":"assistant","content":[{"type":"text","text":"PONG"}],"usage":{"cost":{"total":0.001}},"model":"glm-5.1"},"toolResults":[]}\n',
        b'{"type":"agent_end","messages":[{"role":"user","content":[{"type":"text","text":"ping"}]},{"role":"assistant","content":[{"type":"text","text":"PONG"}],"usage":{"cost":{"total":0.001}},"model":"glm-5.1"}]}\n',
    ]

    class _ScriptPipe:
        def __init__(self, lines: list[bytes]) -> None:
            self._lines = list(lines)

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._lines:
                raise StopAsyncIteration
            return self._lines.pop(0)

    class _EmptyAsyncPipe:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    class _FakeProcess:
        stdout = _ScriptPipe(script_lines)
        stderr = _EmptyAsyncPipe()
        returncode = 0

        async def wait(self):
            return 0

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    request = CodingAgentRequest(prompt="ping", cwd=tmp_path, mcp_servers={}, setting_sources=[])
    run = PiCodingAgent(model="glm-5.1").start(request)

    async def collect_updates():
        return [update async for update in run.updates()]

    updates = asyncio.run(collect_updates())

    # The subprocess must be launched in the request cwd so pi runs there.
    assert captured["kwargs"]["cwd"] == str(tmp_path)
    assert "--mode" in captured["args"]
    assert "json" in captured["args"]
    finals = [update for update in updates if update.final is not None]
    assert finals, "expected at least one final update"
    last = finals[-1].final
    assert last is not None
    assert last.is_error is False
    # turn_end harvests cost=0.001; agent_end re-aggregates to the same total.
    assert last.cost_usd == 0.001
    assert last.num_turns == 1


def test_pi_capabilities_advertise_no_mcp_but_partial_messages() -> None:
    agent = PiCodingAgent()

    # pi intentionally ships no built-in MCP (per upstream docs).
    assert agent.capabilities.supports_mcp is False
    # message_update events carry the full partial each frame.
    assert agent.capabilities.supports_partial_messages is True
    assert agent.capabilities.supports_cost is True
    assert agent.capabilities.supports_abort is True
    assert agent.backend_key == "pi"
    assert agent.display_name == "Pi"
    assert agent.source == "pi_cli"


def test_registry_can_create_pi_backend() -> None:
    registry = create_coding_agent_registry(
        AgentRuntimeConfig(
            default_backend="pi",
            backends=(
                AgentBackendConfig(
                    slug="pi",
                    agent_type="pi",
                    command="/usr/local/bin/pi",
                    model="glm-5.1",
                ),
            ),
        )
    )

    agent = registry.get()

    assert isinstance(agent, PiCodingAgent)
    assert agent.backend_key == "pi"
    assert agent.command == "/usr/local/bin/pi"
    assert agent.model == "glm-5.1"
    # Claude is always registered alongside configured backends.
    assert registry.keys() == ["claude", "pi"]
