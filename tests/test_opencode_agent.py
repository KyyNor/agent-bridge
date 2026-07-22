from __future__ import annotations

from pathlib import Path

from agent_bridge.agent_runtime.adapters.opencode import (
    OpenCodeCodingAgent,
    _build_command,
    _completed_final_from_text,
    _events_from_opencode_row,
)
from agent_bridge.agent_runtime.registry import create_coding_agent_registry
from agent_bridge.agent_runtime.support import build_opencode_mcp_config
from agent_bridge.agent_runtime.types import CodingAgentFinal, CodingAgentRequest
from agent_bridge.core.config import AgentBackendConfig, AgentRuntimeConfig


def test_opencode_build_command_uses_json_dir_model_and_auto() -> None:
    args = _build_command(
        command="opencode",
        prompt="hello",
        cwd="/tmp/project",
        model="anthropic/claude-sonnet-4",
        auto_approve=True,
    )

    assert args == [
        "opencode",
        "run",
        "--format",
        "json",
        "--dir",
        "/tmp/project",
        "--model",
        "anthropic/claude-sonnet-4",
        "--auto",
        "hello",
    ]


def test_opencode_supports_mcp_and_uses_opencode_config_shape() -> None:
    agent = OpenCodeCodingAgent()

    assert agent.capabilities.supports_mcp is True
    assert build_opencode_mcp_config(
        {
            "mcpServers": {
                "agent-bridge": {
                    "type": "http",
                    "url": "http://127.0.0.1:8765/mcp",
                    "headers": {"X-Agent-Bridge-MetaMCP-Profile": "safe"},
                }
            }
        }
    ) == {
        "mcp": {
            "agent-bridge": {
                "type": "remote",
                "url": "http://127.0.0.1:8765/mcp",
                "headers": {"X-Agent-Bridge-MetaMCP-Profile": "safe"},
            }
        }
    }


def test_opencode_text_event_maps_to_agent_message() -> None:
    events, text, final = _events_from_opencode_row(
        {"type": "message.part.updated", "sessionID": "s1", "part": {"type": "text", "text": "hello"}}
    )

    assert text == "hello"
    assert final is None
    assert len(events) == 1
    assert events[0]["agent_name"] == "opencode"
    assert events[0]["source"] == "opencode_cli"
    assert events[0]["kind"] == "agent_message"
    assert events[0]["message"] == "hello"
    assert events[0]["session_id"] == "s1"


def test_opencode_text_event_accepts_nested_text_without_part_type() -> None:
    events, text, final = _events_from_opencode_row(
        {"type": "message.part.updated", "sessionID": "s1", "part": {"text": "nested"}}
    )

    assert text == "nested"
    assert final is None
    assert events[0]["message"] == "nested"


def test_opencode_tool_event_maps_to_call_and_result() -> None:
    events, text, final = _events_from_opencode_row(
        {
            "type": "tool_result",
            "sessionID": "s1",
            "tool": "bash",
            "toolUseID": "tool_1",
            "status": "completed",
            "input": {"command": "pwd"},
            "output": {"stdout": "/tmp"},
        }
    )

    assert text is None
    assert final is None
    assert [event["kind"] for event in events] == ["tool_call", "tool_result"]
    assert events[0]["tool_name"] == "bash"
    assert events[0]["input"] == {"command": "pwd"}
    assert events[1]["status"] == "success"
    assert events[1]["output"] == {"stdout": "/tmp"}


def test_opencode_result_event_maps_to_final() -> None:
    events, text, final = _events_from_opencode_row(
        {"type": "step_finish", "sessionID": "s1", "result": "done", "cost": 0.01}
    )

    assert text is None
    assert final is not None
    assert final.result == "done"
    assert final.session_id == "s1"
    assert final.cost_usd == 0.01
    assert events[0]["kind"] == "result"


def test_opencode_completed_final_prefers_text_over_generic_done_for_schema() -> None:
    request = CodingAgentRequest(
        prompt="make json",
        cwd=Path("/tmp/project"),
        mcp_servers={},
        setting_sources=[],
        output_schema={"type": "object"},
    )
    final = CodingAgentFinal(result="done", session_id="s1")

    completed = _completed_final_from_text(
        final,
        ['```json\n{"summary": "ok", "script": {"code": "def main(envelope):\\n    return {}\\n"}}\n```'],
        request,
    )

    assert completed.result != "done"
    assert '"summary": "ok"' in str(completed.result)
    assert completed.session_id == "s1"


def test_opencode_completed_final_preserves_specific_result() -> None:
    request = CodingAgentRequest(
        prompt="say hi",
        cwd=Path("/tmp/project"),
        mcp_servers={},
        setting_sources=[],
    )
    final = CodingAgentFinal(result="specific final", session_id="s1")

    completed = _completed_final_from_text(final, ["streamed text"], request)

    assert completed is final
    assert completed.result == "specific final"


def test_registry_can_create_opencode_backend() -> None:
    registry = create_coding_agent_registry(
        AgentRuntimeConfig(
            default_backend="opencode",
            backends=(
                AgentBackendConfig(
                    slug="opencode",
                    agent_type="opencode",
                    command="/usr/local/bin/opencode",
                    model="anthropic/claude-sonnet-4",
                ),
            ),
        )
    )

    agent = registry.get()

    assert isinstance(agent, OpenCodeCodingAgent)
    assert agent.command == "/usr/local/bin/opencode"
    assert agent.model == "anthropic/claude-sonnet-4"
