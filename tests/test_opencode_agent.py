from __future__ import annotations

import json

from agent_bridge.agent_runtime.adapters.opencode import (
    OpenCodeCodingAgent,
    _events_from_opencode_response,
    _message_payload,
    _model_payload,
)
from agent_bridge.agent_runtime.registry import create_coding_agent_registry
from agent_bridge.agent_runtime.support import build_opencode_mcp_config
from agent_bridge.agent_runtime.types import CodingAgentRequest
from agent_bridge.core.config import AgentBackendConfig, AgentRuntimeConfig


def test_opencode_message_payload_uses_server_api_and_native_schema(tmp_path) -> None:
    request = CodingAgentRequest(
        prompt="make json",
        cwd=tmp_path,
        mcp_servers={},
        setting_sources=[],
        output_schema={"type": "object", "properties": {"answer": {"type": "string"}}},
        system_prompt_append="follow the project rules",
    )

    assert _message_payload(request, "anthropic/claude-sonnet-4") == {
        "model": {"providerID": "anthropic", "modelID": "claude-sonnet-4"},
        "parts": [{"type": "text", "text": "follow the project rules\n\nmake json"}],
        "format": {
            "type": "json_schema",
            "schema": {"type": "object", "properties": {"answer": {"type": "string"}}},
            "retryCount": 2,
        },
    }
    assert _model_payload("invalid-model") is None


def test_opencode_supports_mcp_and_native_json_schema() -> None:
    agent = OpenCodeCodingAgent()

    assert agent.capabilities.supports_mcp is True
    assert agent.capabilities.supports_native_json_schema is True
    assert agent.capabilities.supports_cost is True
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


def test_opencode_server_response_maps_text_tools_and_structured_output() -> None:
    events, final = _events_from_opencode_response(
        {
            "info": {
                "providerID": "anthropic",
                "modelID": "claude-sonnet-4",
                "cost": 0.12,
            },
            "parts": [
                {"type": "text", "text": "先检查项目。"},
                {
                    "type": "tool",
                    "tool": "bash",
                    "callID": "call_123",
                    "id": "prt_123",
                    "state": {
                        "status": "completed",
                        "input": {"command": "pwd"},
                        "output": {"stdout": "/tmp"},
                        "time": {"start": 1000, "end": 1123},
                    },
                },
                {
                    "type": "tool",
                    "tool": "StructuredOutput",
                    "callID": "call_structured",
                    "state": json_state(
                        {
                            "status": "completed",
                            "input": {"answer": "ok"},
                            "output": "Structured output captured successfully.",
                            "metadata": {"valid": True},
                        }
                    ),
                },
                {
                    "type": "step-finish",
                    "reason": "stop",
                    "tokens": {"input": 10, "output": 20, "reasoning": 3},
                    "cost": 0.12,
                },
            ],
        },
        session_id="ses_123",
    )

    assert [event["kind"] for event in events] == [
        "agent_message",
        "tool_call",
        "tool_result",
        "tool_call",
        "tool_result",
        "status",
    ]
    assert events[1]["tool_use_id"] == "call_123"
    assert events[1]["input"] == {"command": "pwd"}
    assert events[2]["output"] == {"stdout": "/tmp"}
    assert events[2]["duration_ms"] == 123
    assert final.structured_output == {"answer": "ok"}
    assert final.result == '{"answer": "ok"}'
    assert final.session_id == "ses_123"
    assert final.cost_usd == 0.12
    assert final.num_turns == 1
    assert final.model == "anthropic/claude-sonnet-4"


def test_opencode_server_response_accepts_error_info() -> None:
    events, final = _events_from_opencode_response(
        {"info": {"error": {"data": {"message": "provider unavailable"}}}, "parts": []},
        session_id="ses_123",
    )

    assert events[-1]["kind"] == "error"
    assert final.is_error is True
    assert final.result == "provider unavailable"


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


def json_state(value: dict[str, object]) -> str:
    return json.dumps(value)
