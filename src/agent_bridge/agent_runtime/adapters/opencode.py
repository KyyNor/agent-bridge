from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from agent_bridge.agent_runtime.adapters.jsonl_cli import effective_prompt as _effective_prompt
from agent_bridge.agent_runtime.adapters.opencode_server import (
    OpenCodeServerError,
    OpenCodeServerProcess,
)
from agent_bridge.agent_runtime.events import event_record
from agent_bridge.agent_runtime.types import (
    CodingAgentCapabilities,
    CodingAgentFinal,
    CodingAgentRequest,
    CodingAgentRun,
    CodingAgentUpdate,
)


@dataclass
class _OpenCodeRun:
    request: CodingAgentRequest
    command: str
    model: str | None = None
    auto_approve: bool = True
    _server: OpenCodeServerProcess | None = field(default=None, init=False)

    async def updates(self) -> AsyncIterator[CodingAgentUpdate]:
        directory = str(self.request.cwd.resolve())
        server = OpenCodeServerProcess(command=self.command)
        self._server = server
        try:
            await server.start(cwd=self.request.cwd)
            session_response = await server.request_json(
                "POST",
                "/session",
                params={"directory": directory},
                payload={},
            )
            session_id = _session_id(session_response)
            if not session_id:
                raise OpenCodeServerError("OpenCode server 创建会话未返回 session id")

            response = await server.request_json(
                "POST",
                f"/session/{session_id}/message",
                params={"directory": directory},
                payload=_message_payload(self.request, self.model),
            )
            if self.request.on_native_message is not None:
                self.request.on_native_message(response)
            events, final = _events_from_opencode_response(response, session_id=session_id)
            yield CodingAgentUpdate(raw=response, events=events, final=final)
        except asyncio.CancelledError:
            raise
        except OpenCodeServerError as exc:
            yield CodingAgentUpdate(
                events=[_opencode_event("error", status="failed", message=str(exc))],
                final=CodingAgentFinal(is_error=True, result=str(exc)),
            )
        finally:
            await server.close()
            self._server = None

    async def abort(self) -> None:
        if self._server is not None:
            await self._server.abort()


def _message_payload(request: CodingAgentRequest, configured_model: str | None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "parts": [{"type": "text", "text": _effective_prompt(request)}],
    }
    model = _model_payload(request.model or configured_model)
    if model is not None:
        payload["model"] = model
    if request.output_schema:
        payload["format"] = {
            "type": "json_schema",
            "schema": request.output_schema,
            "retryCount": 2,
        }
    return payload


def _model_payload(value: str | None) -> dict[str, str] | None:
    if not value:
        return None
    provider_id, separator, model_id = value.partition("/")
    if not separator or not provider_id or not model_id:
        return None
    return {"providerID": provider_id, "modelID": model_id}


def _events_from_opencode_response(
    response: Any,
    *,
    session_id: str,
) -> tuple[list[dict[str, Any]], CodingAgentFinal]:
    """把 ``POST /session/{id}/message`` 的同步响应投影为统一事件。"""
    if not isinstance(response, dict):
        message = "OpenCode server 返回的消息不是 JSON object"
        return [_opencode_event("error", status="failed", message=message)], CodingAgentFinal(
            is_error=True,
            result=message,
            session_id=session_id,
        )

    info = response.get("info") if isinstance(response.get("info"), dict) else {}
    parts = response.get("parts") if isinstance(response.get("parts"), list) else []
    events: list[dict[str, Any]] = []
    text_parts: list[str] = []
    structured_found = False
    structured_output: Any | None = None
    total_cost = _number(info, "cost", "cost_usd", "total_cost_usd")
    num_turns = _integer(info, "turns", "num_turns")
    if num_turns is None:
        num_turns = 0
    model = _model_name(info)
    error_message = _opencode_error_message(info.get("error"))

    for part in parts:
        if not isinstance(part, dict):
            continue
        part_type = str(part.get("type") or "").lower()
        if part_type == "text":
            text = _string(part.get("text"))
            if text:
                text_parts.append(text)
                events.append(
                    _opencode_event(
                        "agent_message",
                        agent_role="main",
                        message=text,
                        session_id=session_id,
                    )
                )
        elif part_type == "tool":
            tool_events, structured = _tool_events_from_part(part, session_id=session_id)
            events.extend(tool_events)
            if structured is not None:
                structured_found = True
                structured_output = structured
        elif part_type == "step-finish":
            tokens = part.get("tokens") if isinstance(part.get("tokens"), dict) else {}
            cost = _number(part, "cost", "cost_usd", "total_cost_usd")
            if cost is not None:
                total_cost = cost
            num_turns += 1
            events.append(
                _opencode_event(
                    "status",
                    agent_role="main",
                    status=str(part.get("reason") or "step-finished"),
                    message=str(part.get("reason") or "step-finished"),
                    session_id=session_id,
                    usage={
                        key: value
                        for key, value in {
                            "input_tokens": _integer(tokens, "input"),
                            "output_tokens": _integer(tokens, "output"),
                            "reasoning_tokens": _integer(tokens, "reasoning"),
                        }.items()
                        if value is not None
                    },
                    total_cost_usd=cost,
                )
            )

    if num_turns == 0:
        num_turns = None
    if error_message:
        final = CodingAgentFinal(
            is_error=True,
            result=error_message,
            session_id=session_id,
            cost_usd=total_cost,
            num_turns=num_turns,
            model=model,
        )
        events.append(
            _opencode_event(
                "error",
                status="failed",
                message=error_message,
                session_id=session_id,
            )
        )
        return events, final

    result = (
        json.dumps(structured_output, ensure_ascii=False)
        if structured_found
        else "\n".join(text_parts).strip()
    )
    return events, CodingAgentFinal(
        result=result,
        structured_output=structured_output if structured_found else None,
        session_id=session_id,
        cost_usd=total_cost,
        num_turns=num_turns,
        model=model,
    )


def _tool_events_from_part(
    part: dict[str, Any],
    *,
    session_id: str,
) -> tuple[list[dict[str, Any]], Any | None]:
    tool_name = _string(part.get("tool")) or "unknown"
    tool_use_id = _string(part.get("callID")) or _string(part.get("callId")) or _string(part.get("id"))
    state = _mapping(part.get("state"))
    status = (_string(state.get("status")) or "").lower()
    input_value = state.get("input")
    if input_value is None:
        input_value = part.get("input")
    output_value = state.get("output")
    if output_value is None:
        output_value = state.get("error")
    failed = status in {"error", "failed", "failure"} or state.get("error") is not None
    events = [
        _opencode_event(
            "tool_call",
            agent_role="main",
            status="started",
            tool_name=tool_name,
            tool_use_id=tool_use_id,
            **({"input": input_value} if input_value is not None else {}),
            message=f"调用工具 {tool_name}",
            session_id=session_id,
        )
    ]
    if status in {"completed", "success", "error", "failed", "failure"}:
        duration_ms = _tool_duration_ms(state)
        events.append(
            _opencode_event(
                "tool_result",
                agent_role="main",
                status="failed" if failed else "success",
                tool_name=tool_name,
                tool_use_id=tool_use_id,
                **({"output": output_value} if output_value is not None else {}),
                **(
                    {"duration_ms": duration_ms, "duration_status": "provider"}
                    if duration_ms is not None
                    else {}
                ),
                message=f"工具 {tool_name} 调用{'失败' if failed else '成功'}",
                session_id=session_id,
            )
        )

    structured = input_value if tool_name.lower() == "structuredoutput" and not failed else None
    return events, structured


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _tool_duration_ms(state: dict[str, Any]) -> int | None:
    timing = state.get("time")
    if not isinstance(timing, dict):
        return None
    started = timing.get("start")
    finished = timing.get("end")
    if not isinstance(started, int | float) or isinstance(started, bool):
        return None
    if not isinstance(finished, int | float) or isinstance(finished, bool):
        return None
    return max(0, int(finished - started))


def _session_id(response: Any) -> str | None:
    if not isinstance(response, dict):
        return None
    return _string(response.get("id")) or _string(response.get("sessionID"))


def _model_name(info: dict[str, Any]) -> str | None:
    provider = _string(info.get("providerID"))
    model = _string(info.get("modelID"))
    if provider and model:
        return f"{provider}/{model}"
    return model or provider


def _opencode_error_message(error: Any) -> str | None:
    if not error:
        return None
    if isinstance(error, dict):
        data = error.get("data") if isinstance(error.get("data"), dict) else {}
        message = _string(data.get("message")) or _string(error.get("message"))
        if message:
            return message
    return _string(error) or "OpenCode message failed"


def _number(value: Any, *keys: str) -> float | None:
    if not isinstance(value, dict):
        return None
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, int | float) and not isinstance(candidate, bool):
            return float(candidate)
    return None


def _integer(value: Any, *keys: str) -> int | None:
    if not isinstance(value, dict):
        return None
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, int) and not isinstance(candidate, bool):
            return candidate
    return None


def _string(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _opencode_event(kind: str, **values: Any) -> dict[str, Any]:
    return event_record(kind, agent_name="opencode", source="opencode_server", **values)


class OpenCodeCodingAgent:
    source = "opencode_server"
    capabilities = CodingAgentCapabilities(
        supports_mcp=True,
        supports_native_json_schema=True,
        supports_skills=False,
        supports_subagents=False,
        supports_cost=True,
        supports_turn_count=True,
        supports_abort=True,
        supports_partial_messages=False,
    )

    def __init__(
        self,
        *,
        backend_key: str = "opencode",
        command: str = "opencode",
        model: str | None = None,
        auto_approve: bool = True,
    ) -> None:
        self.backend_key = backend_key
        self.display_name = "OpenCode"
        self.command = command
        self.model = model
        self.auto_approve = auto_approve

    def start(self, request: CodingAgentRequest) -> CodingAgentRun:
        return _OpenCodeRun(
            request=request,
            command=self.command,
            model=self.model,
            auto_approve=self.auto_approve,
        )
