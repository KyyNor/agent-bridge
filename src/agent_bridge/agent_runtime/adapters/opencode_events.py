from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from agent_bridge.agent_runtime.events import event_record
from agent_bridge.agent_runtime.types import CodingAgentFinal


@dataclass
class _OpenCodeEventMapper:
    """Map OpenCode V1 SSE events to the backend-neutral event stream.

    This class intentionally owns only the V1 event vocabulary. Process
    lifecycle and SSE framing live in ``opencode_server.py``; a future V2
    client can provide a different mapper without changing ``_OpenCodeRun``.
    """

    session_id: str
    done: bool = False
    _text_parts: dict[str, str] = field(default_factory=dict)
    _text_stream_ids: set[str] = field(default_factory=set)
    _text_delta_ids: set[str] = field(default_factory=set)
    _reasoning_part_ids: set[str] = field(default_factory=set)
    _assistant_message_ids: set[str] = field(default_factory=set)
    _tool_names: dict[str, str] = field(default_factory=dict)
    _tool_call_ids: set[str] = field(default_factory=set)
    _tool_result_ids: set[str] = field(default_factory=set)
    _tool_inputs: dict[str, Any] = field(default_factory=dict)
    _tool_started_at: dict[str, int | float] = field(default_factory=dict)
    _step_started_at: dict[str, int | float] = field(default_factory=dict)
    _reasoning_started_at: dict[str, int | float] = field(default_factory=dict)
    _reasoning_texts: dict[str, str] = field(default_factory=dict)
    _structured_candidates: dict[str, Any] = field(default_factory=dict)
    _structured_found: bool = False
    _structured_output: Any | None = None
    _error_message: str | None = None
    _cost_usd: float | None = None
    _num_turns: int = 0
    _model: str | None = None
    _tokens: dict[str, Any] = field(default_factory=dict)
    _completion_event_emitted: bool = False
    _result_event_emitted: bool = False

    def consume(
        self,
        payload: Any,
    ) -> tuple[list[dict[str, Any]], CodingAgentFinal | None]:
        event_type, properties = _opencode_event_parts(payload)
        if not event_type:
            return [], None
        event_session_id = _string(
            properties.get("sessionID")
            or properties.get("sessionId")
            or properties.get("session_id")
        )
        if event_session_id and event_session_id != self.session_id:
            return [], None

        if event_type == "server.connected":
            return [], None

        if event_type == "message.updated":
            info = properties.get("info")
            if isinstance(info, dict):
                return self._remember_info(info), None
            return [], None

        if event_type == "message.part.updated":
            return self._consume_part_update(properties)

        if event_type == "message.part.delta":
            return self._consume_part_delta(properties)

        if event_type == "session.next.step.started":
            step_id = _string(properties.get("assistantMessageID")) or str(self._num_turns + 1)
            timestamp = _event_timestamp(properties)
            if timestamp is not None:
                self._step_started_at[step_id] = timestamp
            model = _model_ref_name(properties.get("model"))
            if model:
                self._model = model
            return [
                _opencode_event(
                    "status",
                    agent_role="main",
                    status="step-started",
                    message="模型步骤开始",
                    phase="model",
                    step_id=step_id,
                    session_id=self.session_id,
                )
            ], None

        if event_type == "session.next.step.ended":
            self._remember_step_result(properties)
            step_id = _string(properties.get("assistantMessageID")) or str(self._num_turns)
            duration = _duration_from(
                self._step_started_at.pop(step_id, None),
                _event_timestamp(properties),
            )
            values: dict[str, Any] = {
                "agent_role": "main",
                "status": _string(properties.get("finish")) or "step-finished",
                "message": "模型步骤结束",
                "phase": "model",
                "step_id": step_id,
                "session_id": self.session_id,
                "usage": _usage_from_tokens(self._tokens),
                "total_cost_usd": self._cost_usd,
            }
            if duration is not None:
                values.update(duration_ms=duration, duration_status="provider")
            return [_opencode_event("status", **values)], None

        if event_type == "session.next.step.failed":
            message = _opencode_error_message(properties.get("error")) or "OpenCode 模型步骤失败"
            self._error_message = message
            self.done = True
            return [
                _opencode_event(
                    "error",
                    agent_role="main",
                    status="failed",
                    message=message,
                    session_id=self.session_id,
                )
            ], self._final()

        if event_type == "session.next.text.started":
            text_id = _string(properties.get("textID")) or "text"
            self._text_stream_ids.add(text_id)
            return [
                _opencode_event(
                    "status",
                    agent_role="main",
                    status="text-started",
                    message="模型文本输出开始",
                    phase="text",
                    stream_id=text_id,
                    session_id=self.session_id,
                )
            ], None

        if event_type == "session.next.text.delta":
            text_id = _string(properties.get("textID")) or "text"
            delta = properties.get("delta")
            if not isinstance(delta, str) or not delta:
                return [], None
            self._text_stream_ids.add(text_id)
            self._text_parts[text_id] = self._text_parts.get(text_id, "") + delta
            return [
                _opencode_event(
                    "agent_message",
                    agent_role="main",
                    message=delta,
                    partial=True,
                    stream_id=text_id,
                    session_id=self.session_id,
                )
            ], None

        if event_type == "session.next.text.ended":
            text_id = _string(properties.get("textID")) or "text"
            text = properties.get("text")
            if isinstance(text, str):
                self._text_parts[text_id] = text
            return [], None

        if event_type == "session.next.reasoning.started":
            reasoning_id = _string(properties.get("reasoningID")) or "reasoning"
            timestamp = _event_timestamp(properties)
            if timestamp is not None:
                self._reasoning_started_at[reasoning_id] = timestamp
            return [
                _opencode_event(
                    "status",
                    agent_role="main",
                    status="reasoning-started",
                    message="模型推理开始",
                    phase="reasoning",
                    reasoning_id=reasoning_id,
                    session_id=self.session_id,
                )
            ], None

        if event_type == "session.next.reasoning.ended":
            reasoning_id = _string(properties.get("reasoningID")) or "reasoning"
            reasoning_text = _string(properties.get("text")) or self._reasoning_texts.get(reasoning_id)
            duration = _duration_from(
                self._reasoning_started_at.pop(reasoning_id, None),
                _event_timestamp(properties),
            )
            values: dict[str, Any] = {
                "agent_role": "main",
                "status": "reasoning-ended",
                "message": "模型推理结束",
                "phase": "reasoning",
                "reasoning_id": reasoning_id,
                "session_id": self.session_id,
            }
            if reasoning_text:
                values["detail"] = reasoning_text
            if duration is not None:
                values.update(duration_ms=duration, duration_status="provider")
            return [_opencode_event("status", **values)], None

        if event_type == "session.next.tool.input.started":
            call_id = _string(properties.get("callID"))
            if call_id:
                self._tool_names[call_id] = _string(properties.get("name")) or "unknown"
                self._tool_inputs.setdefault(call_id, {})
            return [], None

        if event_type == "session.next.tool.input.delta":
            call_id = _string(properties.get("callID"))
            delta = properties.get("delta")
            if call_id and isinstance(delta, str):
                current = self._tool_inputs.get(call_id, "")
                self._tool_inputs[call_id] = f"{current}{delta}"
            return [], None

        if event_type == "session.next.tool.input.ended":
            call_id = _string(properties.get("callID"))
            text = properties.get("text")
            if call_id and isinstance(text, str):
                self._tool_inputs[call_id] = _coerce_json(text)
            return [], None

        if event_type == "session.next.tool.called":
            call_id = _string(properties.get("callID")) or "unknown"
            tool_name = _string(properties.get("tool")) or self._tool_names.get(call_id) or "unknown"
            input_value = properties.get("input")
            if input_value is None:
                input_value = self._tool_inputs.get(call_id)
            self._tool_names[call_id] = tool_name
            if input_value is not None:
                self._tool_inputs[call_id] = input_value
            timestamp = _event_timestamp(properties)
            if timestamp is not None:
                self._tool_started_at[call_id] = timestamp
            if tool_name.lower() == "structuredoutput" and input_value is not None:
                self._structured_candidates[call_id] = input_value
            return [
                _opencode_event(
                    "tool_call",
                    agent_role="main",
                    status="started",
                    tool_name=tool_name,
                    tool_use_id=call_id,
                    **({"input": input_value} if input_value is not None else {}),
                    message=f"调用工具 {tool_name}",
                    session_id=self.session_id,
                )
            ], None

        if event_type == "session.next.tool.progress":
            call_id = _string(properties.get("callID")) or "unknown"
            tool_name = self._tool_names.get(call_id, "unknown")
            output = _first_tool_output(properties)
            values: dict[str, Any] = {
                "agent_role": "main",
                "status": "tool-progress",
                "message": f"工具 {tool_name} 执行中",
                "phase": "tool",
                "tool_name": tool_name,
                "tool_use_id": call_id,
                "session_id": self.session_id,
            }
            if output is not None:
                values["output"] = output
            return [_opencode_event("status", **values)], None

        if event_type in {"session.next.tool.success", "session.next.tool.failed"}:
            call_id = _string(properties.get("callID")) or "unknown"
            tool_name = self._tool_names.get(call_id, "unknown")
            failed = event_type.endswith("failed")
            output = _first_tool_output(properties)
            if failed:
                output = _opencode_error_message(properties.get("error")) or output
            if tool_name.lower() == "structuredoutput" and not failed:
                # StructuredOutput returns a short acknowledgement in
                # ``result``; the actual schema value is the tool input.
                candidate = self._structured_candidates.get(call_id)
                if candidate is None:
                    candidate = output
                self._set_structured(candidate)
            duration = _duration_from(
                self._tool_started_at.pop(call_id, None),
                _event_timestamp(properties),
            )
            values = {
                "agent_role": "main",
                "status": "failed" if failed else "success",
                "tool_name": tool_name,
                "tool_use_id": call_id,
                **({"output": output} if output is not None else {}),
                "message": f"工具 {tool_name} 调用{'失败' if failed else '成功'}",
                "session_id": self.session_id,
            }
            if duration is not None:
                values.update(duration_ms=duration, duration_status="provider")
            return [_opencode_event("tool_result", **values)], None

        if event_type == "session.status":
            status = _status_name(properties.get("status"))
            values: dict[str, Any] = {
                "agent_role": "main",
                "status": status or "session-status",
                "message": f"OpenCode 会话状态：{status or 'unknown'}",
                "session_id": self.session_id,
            }
            if status == "idle":
                self.done = True
                final = self._final()
                return self._finish_with_result(values, final), final
            return [_opencode_event("status", **values)], None

        if event_type == "session.idle":
            self.done = True
            values = {
                "agent_role": "main",
                "status": "idle",
                "message": "OpenCode 会话完成",
                "session_id": self.session_id,
            }
            final = self._final()
            return self._finish_with_result(values, final), final

        if event_type == "session.error":
            message = _opencode_error_message(properties.get("error")) or "OpenCode 会话失败"
            self._error_message = message
            self.done = True
            return [
                _opencode_event(
                    "error",
                    agent_role="main",
                    status="failed",
                    message=message,
                    session_id=self.session_id,
                )
            ], self._final()

        return [], None

    def _consume_part_update(
        self,
        properties: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], CodingAgentFinal | None]:
        part = properties.get("part")
        if not isinstance(part, dict):
            return [], None
        part_type = str(part.get("type") or "").lower()
        message_id = _string(part.get("messageID")) or _string(properties.get("messageID"))
        if part_type in {"step-start", "step-finish", "reasoning", "text", "tool"} and message_id:
            if part_type == "step-start":
                self._assistant_message_ids.add(message_id)
            elif message_id not in self._assistant_message_ids:
                return [], None

        if part_type == "step-start":
            timestamp = _part_time(part, "start") or _event_timestamp(properties)
            if timestamp is not None and message_id:
                self._step_started_at[message_id] = timestamp
            return [
                _opencode_event(
                    "status",
                    agent_role="main",
                    status="step-started",
                    message="模型步骤开始",
                    phase="model",
                    step_id=message_id,
                    session_id=self.session_id,
                )
            ], None

        if part_type == "step-finish":
            self._remember_step_result(part)
            step_id = message_id or str(self._num_turns)
            duration = _duration_from(
                self._step_started_at.pop(step_id, None),
                _part_time(part, "end") or _event_timestamp(properties),
            )
            values: dict[str, Any] = {
                "agent_role": "main",
                "status": _string(part.get("reason")) or "step-finished",
                "message": "模型步骤结束",
                "phase": "model",
                "step_id": step_id,
                "session_id": self.session_id,
                "usage": _usage_from_tokens(self._tokens),
                "total_cost_usd": self._cost_usd,
            }
            if duration is not None:
                values.update(duration_ms=duration, duration_status="provider")
            return [_opencode_event("status", **values)], None

        if part_type == "reasoning":
            part_id = _string(part.get("id")) or "reasoning"
            self._reasoning_part_ids.add(part_id)
            text = _string(part.get("text"))
            if text:
                self._reasoning_texts[part_id] = text
            duration = _duration_from(_part_time(part, "start"), _part_time(part, "end"))
            values: dict[str, Any] = {
                "agent_role": "main",
                "status": "reasoning-ended" if duration is not None else "reasoning-started",
                "message": "模型推理结束" if duration is not None else "模型推理开始",
                "phase": "reasoning",
                "reasoning_id": part_id,
                "session_id": self.session_id,
            }
            if self._reasoning_texts.get(part_id):
                values["detail"] = self._reasoning_texts[part_id]
            if duration is not None:
                values.update(duration_ms=duration, duration_status="provider")
            return [_opencode_event("status", **values)], None

        if part_type == "text":
            text_id = _string(part.get("id")) or "text"
            text = _string(part.get("text"))
            if text:
                self._text_parts[text_id] = text
            return [], None
        if part_type != "tool":
            return [], None
        tool_name = _string(part.get("tool")) or "unknown"
        call_id = _string(part.get("callID")) or _string(part.get("id")) or "unknown"
        state = _mapping(part.get("state"))
        state_status = (_string(state.get("status")) or "").lower()
        input_value = state.get("input")
        if input_value is None:
            input_value = part.get("input")
        self._tool_names[call_id] = tool_name
        if input_value is not None:
            self._tool_inputs[call_id] = input_value
        if tool_name.lower() == "structuredoutput" and state_status != "error":
            self._structured_candidates[call_id] = input_value

        events: list[dict[str, Any]] = []
        if state_status in {"pending", "running", "completed", "error", "failed", "failure"}:
            # ``pending`` often carries an empty input. Wait for the first
            # running/completed snapshot so the canonical tool_call contains
            # the real input shown to the user.
            if call_id not in self._tool_call_ids and state_status != "pending":
                self._tool_call_ids.add(call_id)
                started = _part_time(state, "start")
                if started is not None:
                    self._tool_started_at[call_id] = started
                events.append(
                    _opencode_event(
                        "tool_call",
                        agent_role="main",
                        status="started",
                        tool_name=tool_name,
                        tool_use_id=call_id,
                        **({"input": input_value} if input_value is not None else {}),
                        message=f"调用工具 {tool_name}",
                        session_id=self.session_id,
                    )
                )
        if state_status in {"completed", "error", "failed", "failure"} and call_id not in self._tool_result_ids:
            self._tool_result_ids.add(call_id)
            failed = state_status in {"error", "failed", "failure"} or state.get("error") is not None
            output_value = state.get("output")
            if output_value is None:
                output_value = state.get("error")
            if tool_name.lower() == "structuredoutput" and not failed:
                self._set_structured(self._structured_candidates.get(call_id))
            started = self._tool_started_at.pop(call_id, None)
            duration = _tool_duration_ms(state)
            if duration is None:
                duration = _duration_from(
                    started,
                    _part_time(state, "end"),
                )
            values = {
                "agent_role": "main",
                "status": "failed" if failed else "success",
                "tool_name": tool_name,
                "tool_use_id": call_id,
                **({"output": output_value} if output_value is not None else {}),
                **(
                    {"duration_ms": duration, "duration_status": "provider"}
                    if duration is not None
                    else {}
                ),
                "message": f"工具 {tool_name} 调用{'失败' if failed else '成功'}",
                "session_id": self.session_id,
            }
            events.append(_opencode_event("tool_result", **values))
        return events, None

    def _consume_part_delta(
        self,
        properties: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], CodingAgentFinal | None]:
        message_id = _string(properties.get("messageID"))
        if message_id and message_id not in self._assistant_message_ids:
            return [], None
        part_id = _string(properties.get("partID")) or "part"
        delta = properties.get("delta")
        if not isinstance(delta, str) or not delta:
            return [], None
        field_name = _string(properties.get("field")) or ""
        if part_id in self._reasoning_part_ids or "reasoning" in field_name.lower():
            self._reasoning_texts[part_id] = self._reasoning_texts.get(part_id, "") + delta
            return [], None
        if field_name not in {"text", "content"}:
            return [], None
        if part_id not in self._text_delta_ids:
            self._text_delta_ids.add(part_id)
            self._text_parts[part_id] = ""
        self._text_stream_ids.add(part_id)
        self._text_parts[part_id] += delta
        return [
            _opencode_event(
                "agent_message",
                agent_role="main",
                message=delta,
                partial=True,
                stream_id=part_id,
                session_id=self.session_id,
            )
        ], None

    def _remember_info(self, info: dict[str, Any]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        message_id = _string(info.get("id"))
        if _string(info.get("role")) == "assistant" and message_id:
            self._assistant_message_ids.add(message_id)
        cost = _number(info, "cost", "cost_usd", "total_cost_usd")
        if cost is not None:
            self._cost_usd = cost
        model = _model_name(info)
        if model:
            self._model = model
        tokens = info.get("tokens")
        if isinstance(tokens, dict):
            self._tokens = dict(tokens)
        structured = info.get("structured")
        if structured is not None:
            self._set_structured(structured)
        error_message = _opencode_error_message(info.get("error"))
        if error_message:
            self._error_message = error_message
        info_time = info.get("time")
        if (
            _string(info.get("role")) == "assistant"
            and isinstance(info_time, dict)
            and info_time.get("completed") is not None
            and info_time.get("created") is not None
            and not self._completion_event_emitted
        ):
            duration = _duration_from(info_time.get("created"), info_time.get("completed"))
            if duration is not None:
                self._completion_event_emitted = True
                events.append(
                    _opencode_event(
                        "stage",
                        agent_role="main",
                        stage_name="opencode.model",
                        status="success" if not self._error_message else "failed",
                        message="OpenCode 模型响应完成",
                        phase="model",
                        duration_ms=duration,
                        duration_status="provider",
                        session_id=self.session_id,
                    )
                )
        return events

    def _remember_step_result(self, properties: dict[str, Any]) -> None:
        cost = _number(properties, "cost", "cost_usd", "total_cost_usd")
        if cost is not None:
            self._cost_usd = cost
        tokens = properties.get("tokens")
        if isinstance(tokens, dict):
            self._tokens = dict(tokens)
        self._num_turns += 1

    def _set_structured(self, value: Any) -> None:
        decoded = _coerce_json(value)
        if decoded is None:
            return
        self._structured_found = True
        self._structured_output = decoded

    def _finish_with_result(
        self,
        status_values: dict[str, Any],
        final: CodingAgentFinal,
    ) -> list[dict[str, Any]]:
        events = [_opencode_event("status", **status_values)]
        if self._result_event_emitted:
            return events
        self._result_event_emitted = True
        events.append(
            _opencode_event(
                "result",
                agent_role="main",
                status="failed" if final.is_error else "success",
                message=final.result or ("OpenCode 运行失败" if final.is_error else "done"),
                session_id=self.session_id,
                total_cost_usd=final.cost_usd,
                num_turns=final.num_turns,
            )
        )
        return events

    def _final(self) -> CodingAgentFinal:
        if self._error_message:
            return CodingAgentFinal(
                is_error=True,
                result=self._error_message,
                session_id=self.session_id,
                cost_usd=self._cost_usd,
                num_turns=self._num_turns or None,
                model=self._model,
            )
        text = "".join(self._text_parts.values()).strip()
        result = (
            json.dumps(self._structured_output, ensure_ascii=False)
            if self._structured_found
            else text
        )
        return CodingAgentFinal(
            result=result,
            structured_output=self._structured_output if self._structured_found else None,
            session_id=self.session_id,
            cost_usd=self._cost_usd,
            num_turns=self._num_turns or None,
            model=self._model,
        )


def _opencode_event_parts(payload: Any) -> tuple[str | None, dict[str, Any]]:
    if not isinstance(payload, dict):
        return None, {}
    root = payload
    if isinstance(root.get("payload"), dict):
        root = root["payload"]
    if root.get("type") == "sync" and isinstance(root.get("syncEvent"), dict):
        root = root["syncEvent"]
    event_type = _string(root.get("type")) or _string(root.get("event"))
    properties = root.get("properties")
    if not isinstance(properties, dict):
        properties = root.get("data")
    if not isinstance(properties, dict):
        properties = {}
    return event_type, properties


def _event_timestamp(properties: dict[str, Any]) -> int | float | None:
    value = properties.get("timestamp")
    if value is None:
        value = properties.get("time")
    if isinstance(value, int | float) and not isinstance(value, bool):
        return value
    return None


def _part_time(part: dict[str, Any], key: str) -> int | float | None:
    timing = part.get("time")
    if not isinstance(timing, dict):
        return None
    value = timing.get(key)
    if isinstance(value, int | float) and not isinstance(value, bool):
        return value
    return None


def _duration_from(
    started: int | float | None,
    finished: int | float | None,
) -> int | None:
    if started is None or finished is None:
        return None
    return max(0, int(finished - started))


def _usage_from_tokens(tokens: dict[str, Any]) -> dict[str, Any]:
    usage: dict[str, Any] = {}
    mapping = {
        "input": "input_tokens",
        "output": "output_tokens",
        "reasoning": "reasoning_tokens",
        "total": "total_tokens",
        "cache": "cache",
    }
    for source, target in mapping.items():
        value = tokens.get(source)
        if value is not None:
            usage[target] = value
    return usage


def _model_ref_name(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    provider = _string(value.get("providerID")) or _string(value.get("provider_id"))
    model = _string(value.get("modelID")) or _string(value.get("model_id")) or _string(value.get("id"))
    if provider and model:
        return f"{provider}/{model}"
    return model or provider


def _status_name(value: Any) -> str | None:
    if isinstance(value, str):
        return value.lower()
    if isinstance(value, dict):
        return _string(value.get("type")) or _string(value.get("status"))
    return None


def _coerce_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value if value else None


def _first_tool_output(properties: dict[str, Any]) -> Any:
    for key in ("result", "structured", "content", "output"):
        if properties.get(key) is not None:
            return properties[key]
    return None


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
