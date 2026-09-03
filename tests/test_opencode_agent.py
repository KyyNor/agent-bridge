from __future__ import annotations

import asyncio
import json

from agent_bridge.agent_runtime.adapters.opencode import (
    OpenCodeCodingAgent,
    _OpenCodeEventMapper,
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


def test_opencode_message_payload_includes_reasoning_variant(tmp_path) -> None:
    request = CodingAgentRequest(
        prompt="make json",
        cwd=tmp_path,
        mcp_servers={},
        setting_sources=[],
    )

    payload = _message_payload(request, "anthropic/claude-sonnet-4", "high")

    assert payload["variant"] == "high"
    assert _message_payload(request, "anthropic/claude-sonnet-4").get("variant") is None


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


def test_opencode_sse_events_map_tools_phases_and_final_output() -> None:
    mapper = _OpenCodeEventMapper(session_id="ses_123")
    payloads = [
        {
            "type": "server.connected",
            "properties": {},
        },
        {
            "type": "session.next.step.started",
            "properties": {
                "timestamp": 100,
                "sessionID": "ses_123",
                "assistantMessageID": "msg_123",
                "model": {"providerID": "anthropic", "modelID": "claude-sonnet-4"},
            },
        },
        {
            "type": "session.next.reasoning.started",
            "properties": {
                "timestamp": 110,
                "sessionID": "ses_123",
                "reasoningID": "reason_123",
            },
        },
        {
            "type": "session.next.reasoning.ended",
            "properties": {
                "timestamp": 210,
                "sessionID": "ses_123",
                "reasoningID": "reason_123",
                "text": "internal reasoning must remain out of the user timeline",
            },
        },
        {
            "type": "session.next.tool.called",
            "properties": {
                "timestamp": 300,
                "sessionID": "ses_123",
                "assistantMessageID": "msg_123",
                "callID": "call_structured",
                "tool": "StructuredOutput",
                "input": {"answer": "ok"},
                "provider": {"executed": True},
            },
        },
        {
            "type": "session.next.tool.success",
            "properties": {
                "timestamp": 350,
                "sessionID": "ses_123",
                "assistantMessageID": "msg_123",
                "callID": "call_structured",
                "structured": {},
                "content": [],
                "provider": {"executed": True},
            },
        },
        {
            "type": "session.next.step.ended",
            "properties": {
                "timestamp": 1000,
                "sessionID": "ses_123",
                "assistantMessageID": "msg_123",
                "finish": "tool-calls",
                "cost": 0.12,
                "tokens": {"input": 10, "output": 20, "reasoning": 3},
            },
        },
        {
            "type": "session.idle",
            "properties": {"sessionID": "ses_123"},
        },
    ]

    events = []
    final = None
    for payload in payloads:
        new_events, maybe_final = mapper.consume(payload)
        events.extend(new_events)
        if maybe_final is not None:
            final = maybe_final

    assert [event["kind"] for event in events] == [
        "status",
        "status",
        "status",
        "tool_call",
        "tool_result",
        "status",
        "status",
        "result",
    ]
    assert events[2]["duration_ms"] == 100
    assert events[3]["input"] == {"answer": "ok"}
    assert events[4]["duration_ms"] == 50
    assert events[5]["duration_ms"] == 900
    assert events[5]["usage"] == {
        "input_tokens": 10,
        "output_tokens": 20,
        "reasoning_tokens": 3,
    }
    assert events[7]["message"] == '{"answer": "ok"}'
    assert final is not None
    assert final.structured_output == {"answer": "ok"}
    assert final.result == '{"answer": "ok"}'
    assert final.cost_usd == 0.12
    assert final.num_turns == 1
    assert final.model == "anthropic/claude-sonnet-4"


def test_opencode_run_uses_async_prompt_and_sse(monkeypatch, tmp_path) -> None:
    from agent_bridge.agent_runtime.adapters import opencode as opencode_module

    class FakeServer:
        instances: list["FakeServer"] = []

        def __init__(self, *, command: str) -> None:
            self.command = command
            self.calls: list[tuple[str, str]] = []
            self.__class__.instances.append(self)

        async def start(self, *, cwd) -> None:
            self.calls.append(("start", str(cwd)))

        async def request_json(self, method, path, *, params=None, payload=None):
            self.calls.append((method, path))
            return {"id": "ses_123"}

        async def request_no_content(self, method, path, *, params=None, payload=None):
            self.calls.append((method, path))

        async def stream_json_events(self, path, *, params=None):
            yield {"type": "server.connected", "properties": {}}
            yield {
                "type": "session.next.text.delta",
                "properties": {"sessionID": "ses_123", "textID": "text_1", "delta": "done"},
            }
            yield {"type": "session.idle", "properties": {"sessionID": "ses_123"}}

        async def close(self) -> None:
            self.calls.append(("close", ""))

        async def abort(self) -> None:
            self.calls.append(("abort", ""))

    monkeypatch.setattr(opencode_module, "OpenCodeServerProcess", FakeServer)
    request = CodingAgentRequest(prompt="hello", cwd=tmp_path, mcp_servers={}, setting_sources=[])
    run = OpenCodeCodingAgent().start(request)

    async def collect_updates():
        return [update async for update in run.updates()]

    updates = asyncio.run(collect_updates())
    assert updates[-1].final is not None
    assert updates[-1].final.result == "done"
    assert any(event["kind"] == "agent_message" for update in updates for event in update.events)
    assert FakeServer.instances[-1].calls == [
        ("start", str(tmp_path)),
        ("POST", "/session"),
        ("POST", "/session/ses_123/prompt_async"),
        ("close", ""),
    ]


def test_opencode_message_parts_wait_for_real_tool_input() -> None:
    mapper = _OpenCodeEventMapper(session_id="ses_123")
    mapper.consume(
        {
            "type": "message.part.updated",
            "properties": {
                "sessionID": "ses_123",
                "part": {
                    "id": "prt_step",
                    "messageID": "msg_assistant",
                    "type": "step-start",
                },
            },
        }
    )
    pending_events, _ = mapper.consume(
        {
            "type": "message.part.updated",
            "properties": {
                "sessionID": "ses_123",
                "part": {
                    "id": "prt_tool",
                    "messageID": "msg_assistant",
                    "type": "tool",
                    "callID": "call_pwd",
                    "tool": "bash",
                    "state": {"status": "pending", "input": {}},
                },
            },
        }
    )
    call_events, _ = mapper.consume(
        {
            "type": "message.part.updated",
            "properties": {
                "sessionID": "ses_123",
                "part": {
                    "id": "prt_tool",
                    "messageID": "msg_assistant",
                    "type": "tool",
                    "callID": "call_pwd",
                    "tool": "bash",
                    "state": {
                        "status": "running",
                        "input": {"command": "pwd"},
                        "time": {"start": 100},
                    },
                },
            },
        }
    )
    result_events, _ = mapper.consume(
        {
            "type": "message.part.updated",
            "properties": {
                "sessionID": "ses_123",
                "part": {
                    "id": "prt_tool",
                    "messageID": "msg_assistant",
                    "type": "tool",
                    "callID": "call_pwd",
                    "tool": "bash",
                    "state": {
                        "status": "completed",
                        "input": {"command": "pwd"},
                        "output": "/tmp\n",
                        "time": {"start": 100, "end": 108},
                    },
                },
            },
        }
    )

    assert pending_events == []
    assert call_events[0]["kind"] == "tool_call"
    assert call_events[0]["input"] == {"command": "pwd"}
    assert result_events[0]["kind"] == "tool_result"
    assert result_events[0]["duration_ms"] == 8


def test_opencode_reasoning_part_exposes_provider_text_as_detail() -> None:
    mapper = _OpenCodeEventMapper(session_id="ses_123")
    mapper.consume(
        {
            "type": "message.part.updated",
            "properties": {
                "sessionID": "ses_123",
                "part": {"id": "prt_step", "messageID": "msg_assistant", "type": "step-start"},
            },
        }
    )
    reasoning_events, _ = mapper.consume(
        {
            "type": "message.part.updated",
            "properties": {
                "sessionID": "ses_123",
                "part": {
                    "id": "prt_reasoning",
                    "messageID": "msg_assistant",
                    "type": "reasoning",
                    "text": "先读取仓库信息，再整理最终答案。",
                    "time": {"start": 100, "end": 140},
                },
            },
        }
    )

    mapper.consume(
        {
            "type": "session.idle",
            "properties": {"sessionID": "ses_123"},
        }
    )

    reasoning_event = next(event for event in reasoning_events if event.get("status") == "reasoning-ended")
    assert reasoning_event["detail"] == "先读取仓库信息，再整理最终答案。"
    assert reasoning_event["duration_ms"] == 40


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
