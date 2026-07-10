from __future__ import annotations

from agent_bridge.agent_runtime.adapters.codex import (
    CodexCodingAgent,
    _build_command,
    _events_from_codex_row,
)
from agent_bridge.agent_runtime.registry import create_coding_agent_registry
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
