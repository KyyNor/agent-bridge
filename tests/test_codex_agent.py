from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from agent_bridge.agent_runtime.adapters import codex as codex_module
from agent_bridge.agent_runtime.adapters.codex import (
    CodexCodingAgent,
    _build_command,
    _events_from_codex_row,
    _schema_for_codex,
)
from agent_bridge.agent_runtime.registry import create_coding_agent_registry
from agent_bridge.agent_runtime.types import CodingAgentRequest
from agent_bridge.core.config import AgentBackendConfig, AgentRuntimeConfig


def test_codex_build_command_uses_exec_json_cd_model_and_schema() -> None:
    args = _build_command(
        command="codex",
        prompt="hello",
        cwd="/tmp/project",
        model="gpt-5",
        schema_path="/tmp/schema.json",
    )

    assert args == [
        "codex",
        "exec",
        "--json",
        "--cd",
        "/tmp/project",
        "--skip-git-repo-check",
        "--model",
        "gpt-5",
        "--dangerously-bypass-approvals-and-sandbox",
        "--output-schema",
        "/tmp/schema.json",
        "hello",
    ]


def test_codex_agent_message_event_maps_from_json_row() -> None:
    events, text, final = _events_from_codex_row(
        {"type": "agent_message", "message": "hello", "session_id": "s1"}
    )

    assert text == "hello"
    assert final is None
    assert events[0]["agent_name"] == "codex"
    assert events[0]["source"] == "codex_cli"
    assert events[0]["kind"] == "agent_message"
    assert events[0]["message"] == "hello"
    assert events[0]["session_id"] == "s1"


def test_codex_result_event_maps_to_final() -> None:
    events, text, final = _events_from_codex_row(
        {"type": "result", "message": '{"answer": 42}', "session_id": "s1"}
    )

    assert text is None
    assert final is not None
    assert final.result == '{"answer": 42}'
    assert final.session_id == "s1"
    assert events[0]["kind"] == "result"


def test_codex_tool_event_keeps_input_or_output_payload() -> None:
    call_events, _, _ = _events_from_codex_row(
        {
            "type": "tool_call",
            "tool": "shell",
            "tool_use_id": "call_1",
            "input": {"command": "pwd"},
        }
    )
    result_events, _, _ = _events_from_codex_row(
        {
            "type": "tool_result",
            "tool": "shell",
            "tool_use_id": "call_1",
            "output": {"stdout": "/tmp"},
            "status": "completed",
        }
    )

    assert call_events[0]["input"] == {"command": "pwd"}
    assert result_events[0]["output"] == {"stdout": "/tmp"}


def test_codex_schema_requires_all_declared_object_properties() -> None:
    schema = {
        "type": "object",
        "required": ["summary"],
        "properties": {
            "summary": {"type": "string"},
            "notes": {"type": "array", "items": {"type": "string"}},
            "script": {
                "type": "object",
                "required": ["code"],
                "properties": {
                    "code": {"type": "string"},
                    "description": {"type": "string"},
                },
            },
        },
    }

    normalized = _schema_for_codex(schema)

    assert normalized["required"] == ["summary", "notes", "script"]
    assert normalized["properties"]["script"]["required"] == ["code", "description"]
    assert schema["required"] == ["summary"]


def test_codex_run_closes_stdin(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class _EmptyAsyncPipe:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    class _FakeProcess:
        stdout = _EmptyAsyncPipe()
        stderr = _EmptyAsyncPipe()
        returncode = 0

        async def wait(self):
            return 0

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    request = CodingAgentRequest(prompt="hello", cwd=tmp_path, mcp_servers={}, setting_sources=[])
    run = CodexCodingAgent().start(request)

    async def collect_updates():
        return [update async for update in run.updates()]

    updates = asyncio.run(collect_updates())

    assert captured["kwargs"]["stdin"] == subprocess.DEVNULL
    assert updates[-1].final is not None


def test_codex_schema_fallback_emits_structured_output_event(monkeypatch, tmp_path: Path) -> None:
    class _FakeCli:
        def __init__(self, *args, **kwargs):
            pass

        async def start(self):
            return None

        async def rows(self):
            yield {"type": "agent_message", "message": '{"answer": 42}'}

        async def wait(self):
            return 0

        async def close(self):
            return None

        async def abort(self):
            return None

        def stderr_summary(self):
            return ""

    monkeypatch.setattr(codex_module, "JsonlCliProcess", _FakeCli)
    schema = {"type": "object", "properties": {"answer": {"type": "number"}}}
    request = CodingAgentRequest(
        prompt="compute",
        cwd=tmp_path,
        mcp_servers={},
        setting_sources=[],
        output_schema=schema,
    )

    updates = asyncio.run(_collect_updates(CodexCodingAgent().start(request)))

    assert updates[0].final is None
    assert updates[0].events[0]["kind"] == "agent_message"

    # The fallback final update is the second update and carries the derived
    # event used by the timeline; the original message remains untouched.
    assert updates[-1].final is not None
    assert updates[-1].final.structured_output == {"answer": 42}
    assert updates[-1].events[0]["kind"] == "structured_output"
    assert updates[-1].events[0]["output"] == {"answer": 42}
    assert updates[-1].events[0]["output_content_type"] == "application/json"


async def _collect_updates(run):
    return [update async for update in run.updates()]


def test_registry_can_create_codex_backend() -> None:
    registry = create_coding_agent_registry(
        AgentRuntimeConfig(
            default_backend="codex",
            backends=(
                AgentBackendConfig(
                    slug="codex",
                    agent_type="codex",
                    command="/opt/homebrew/bin/codex",
                    model="gpt-5",
                ),
            ),
        )
    )

    agent = registry.get()

    assert isinstance(agent, CodexCodingAgent)
    assert agent.command == "/opt/homebrew/bin/codex"
    assert agent.model == "gpt-5"


def test_registry_rejects_backend_that_borrows_another_type_slug() -> None:
    with pytest.raises(ValueError, match="slug 必须与 type 完全一致"):
        create_coding_agent_registry(
            AgentRuntimeConfig(
                backends=(
                    AgentBackendConfig(slug="claude", agent_type="codex"),
                ),
            )
        )
