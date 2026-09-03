from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from agent_bridge.agent_runtime.adapters.jsonl_cli import (
    JsonlCliProcess,
    effective_prompt as _effective_prompt,
    extract_json_object as _extract_json,
    first_string as _first_string,
    first_value as _first_value,
    is_generic_final_result as _is_generic_final_result,
    joined_text as _joined_text,
    row_is_error as _is_error,
    walk_values as _walk_values,
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
class _PiRun:
    request: CodingAgentRequest
    command: str
    model: str | None = None
    provider: str | None = None
    thinking: str | None = None
    _cli: JsonlCliProcess | None = field(default=None, init=False)

    async def updates(self) -> AsyncIterator[CodingAgentUpdate]:
        prompt = _effective_prompt(self.request)
        args = _build_command(
            command=self.command,
            prompt=prompt,
            model=self.request.model or self.model,
            provider=self.provider,
            thinking=self.thinking,
        )
        cli = JsonlCliProcess(
            request=self.request,
            args=args,
            cwd=str(self.request.cwd),
        )
        self._cli = cli
        await cli.start()
        final_text_parts: list[str] = []
        final: CodingAgentFinal | None = None
        try:
            async for raw in cli.rows():
                events, maybe_text, maybe_final = _events_from_pi_row(raw)
                if maybe_text:
                    final_text_parts.append(maybe_text)
                if maybe_final is not None:
                    final = maybe_final
                yield CodingAgentUpdate(raw=raw, events=events, final=maybe_final)
            return_code = await cli.wait()
            if return_code != 0:
                message = cli.stderr_summary() or f"pi exited with status {return_code}"
                yield CodingAgentUpdate(
                    events=[_pi_event("error", status="failed", message=message)],
                    final=CodingAgentFinal(is_error=True, result=message),
                )
                return
            if final is None:
                yield CodingAgentUpdate(
                    final=CodingAgentFinal(result=_joined_text(final_text_parts))
                )
            else:
                completed_final = _completed_final_from_text(final, final_text_parts, self.request)
                if completed_final is not final:
                    yield CodingAgentUpdate(final=completed_final)
        finally:
            await cli.close()

    async def abort(self) -> None:
        if self._cli is not None:
            await self._cli.abort()


def _build_command(
    *,
    command: str,
    prompt: str,
    model: str | None,
    provider: str | None,
    thinking: str | None,
) -> list[str]:
    # pi --mode json -p streams AgentSessionEvent JSONL to stdout and exits.
    # --no-session keeps the run ephemeral (Agent Bridge owns run lifecycle).
    args = [command, "--mode", "json", "-p", "--no-session"]
    resolved_provider, resolved_model = _resolve_model(provider, model)
    if resolved_provider:
        args.extend(["--provider", resolved_provider])
    if resolved_model:
        args.extend(["--model", resolved_model])
    if thinking:
        args.extend(["--thinking", thinking])
    args.append(prompt)
    return args


def _resolve_model(provider: str | None, model: str | None) -> tuple[str | None, str | None]:
    """Split a ``provider/id`` model spec the way pi's --model flag accepts it.

    pi natively understands ``--model provider/id``; when a caller already
    configures the model that way we forward it verbatim and let pi split it,
    instead of also emitting a redundant ``--provider``.
    """
    if not model:
        return provider, None
    if "/" in model:
        # Defer the split to pi by passing the full spec as --model.
        return None, model
    return provider, model


def _completed_final_from_text(
    final: CodingAgentFinal,
    final_text_parts: list[str],
    request: CodingAgentRequest,
) -> CodingAgentFinal:
    """Prefer the assistant's final text over a generic status final.

    pi's json mode emits an ``agent_end`` event whose ``messages`` array is the
    authoritative transcript, but the last assistant ``text`` content is the
    payload callers actually want. When the structured run needs JSON and the
    final result does not parse, fall back to the accumulated streamed text
    (mirrors the opencode/codex adapters).
    """
    text = _joined_text(final_text_parts)
    if not text or final.is_error:
        return final
    final_result = str(final.result or "").strip()
    if _is_generic_final_result(final_result):
        return _replace_result(final, text)
    if request.output_schema and _extract_json(final_result) is None and _extract_json(text) is not None:
        return _replace_result(final, text)
    return final


def _replace_result(final: CodingAgentFinal, text: str) -> CodingAgentFinal:
    return CodingAgentFinal(
        is_error=final.is_error,
        result=text,
        structured_output=_extract_json(text),
        subtype=final.subtype,
        session_id=final.session_id,
        cost_usd=final.cost_usd,
        num_turns=final.num_turns,
        model=final.model,
    )


# pi json-mode event taxonomy (observed against zai/glm-5.1, see docs/json.md):
#   session, agent_start, turn_start, message_start, message_update,
#   message_end, turn_end, agent_end, tool_execution_start/update/end,
#   queue_update, compaction_*, auto_retry_*, extension_error.
def _events_from_pi_row(
    row: dict[str, Any],
) -> tuple[list[dict[str, Any]], str | None, CodingAgentFinal | None]:
    row_type = str(row.get("type") or row.get("event") or row.get("kind") or "")
    session_id = _first_string(row, "session_id", "sessionId", "sessionID", "id")

    if row_type == "session":
        # Header line; nothing to surface beyond the id (already captured).
        return [], None, None

    if row_type == "message_update":
        event = row.get("assistantMessageEvent")
        if not isinstance(event, dict):
            return [], None, None
        delta_type = str(event.get("type") or "")
        # Only surface assistant text deltas as agent_message events; thinking
        # deltas are intentionally dropped to match the other CLI adapters.
        if delta_type in {"text_start", "text_delta", "text_end"}:
            text = _text_from_assistant_event(event) or _assistant_text(row.get("message"))
            if not text:
                return [], None, None
            return [
                _pi_event(
                    "agent_message",
                    agent_role="main",
                    message=text,
                    session_id=session_id,
                )
            ], text, None
        return [], None, None

    if row_type in {"message_start", "message_end"}:
        # Non-streaming message boundaries carry no new user-visible content.
        return [], None, None

    if row_type in {"tool_execution_start", "tool_execution_update", "tool_execution_end"}:
        tool_name = _first_string(row, "toolName", "tool_name", "toolName") or "unknown"
        tool_use_id = _first_string(row, "toolCallId", "tool_call_id", "toolUseID", "id")
        if row_type == "tool_execution_start":
            input_value = _first_value(row, "args", "input", "arguments", "params")
            return [
                _pi_event(
                    "tool_call",
                    agent_role="main",
                    status="started",
                    tool_name=tool_name,
                    tool_use_id=tool_use_id,
                    **({"input": input_value} if input_value is not None else {}),
                    message=f"调用工具 {tool_name}",
                    session_id=session_id,
                )
            ], None, None
        if row_type == "tool_execution_end":
            is_error = bool(row.get("isError"))
            output_value = _first_value(row, "result", "output", "content", "response")
            return [
                _pi_event(
                    "tool_result",
                    agent_role="main",
                    status="failed" if is_error else "success",
                    tool_name=tool_name,
                    tool_use_id=tool_use_id,
                    **({"output": output_value} if output_value is not None else {}),
                    message=f"工具 {tool_name} 调用{'失败' if is_error else '成功'}",
                    session_id=session_id,
                )
            ], None, None
        # tool_execution_update is a streaming-progress event; suppress it to
        # avoid a duplicate "started" per partial output frame (matches the
        # other CLI adapters, which hide mid-tool streaming rows).
        return [], None, None

    if row_type == "turn_end":
        # turn_end carries the completed assistant message + usage; the final
        # agent_end event is what we treat as authoritative, so just harvest
        # cost/turn counters here for callers that never reach agent_end.
        message = row.get("message")
        cost_usd = _usage_cost(message) if isinstance(message, dict) else None
        model = _usage_model(message) if isinstance(message, dict) else None
        text = _assistant_text(message)
        final = CodingAgentFinal(
            result=text or "",
            session_id=session_id,
            cost_usd=cost_usd,
            num_turns=1,
            model=model,
        )
        return [
            _pi_event(
                "status",
                agent_role="main",
                status="turn_end",
                message="回合结束",
                session_id=session_id,
                total_cost_usd=cost_usd,
                num_turns=1,
            )
        ], text or None, final

    if row_type == "agent_end":
        messages = row.get("messages")
        assistant_text = _last_assistant_text(messages)
        cost_usd, num_turns, model = _aggregate_usage(messages)
        final = CodingAgentFinal(
            result=assistant_text or "",
            session_id=session_id,
            cost_usd=cost_usd,
            num_turns=num_turns,
            model=model,
        )
        return [
            _pi_event(
                "result",
                agent_role="main",
                status="success",
                message=assistant_text or "done",
                session_id=session_id,
                total_cost_usd=cost_usd,
                num_turns=num_turns,
            )
        ], None, final

    if row_type in {"error", "failed"} or _is_error(row):
        message = _text_from_row(row) or str(row.get("error") or row.get("message") or row_type)
        return [
            _pi_event("error", status="failed", message=message, session_id=session_id)
        ], None, CodingAgentFinal(is_error=True, result=message, session_id=session_id)

    # Fallback: surface any recognizable text as an agent message.
    text = _text_from_row(row)
    if text:
        return [
            _pi_event(
                "agent_message",
                agent_role="main",
                message=text,
                session_id=session_id,
            )
        ], text, None
    return [], None, None


def _pi_event(kind: str, **values: Any) -> dict[str, Any]:
    return event_record(kind, agent_name="pi", source="pi_cli", **values)


def _text_from_assistant_event(event: dict[str, Any]) -> str:
    # text_end carries the finalized content directly; text_delta carries a
    # fragment in ``delta``. We surface whichever is present so the caller can
    # accumulate either form.
    if event.get("type") == "text_end":
        content = event.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    delta = event.get("delta")
    if isinstance(delta, str) and delta:
        return delta
    return ""


def _assistant_text(message: Any) -> str:
    """Pull the concatenated assistant text content out of a pi message."""
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n".join(part for part in parts if part).strip()


def _last_assistant_text(messages: Any) -> str:
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "assistant":
            text = _assistant_text(message)
            if text:
                return text
    return ""


def _usage_cost(message: Any) -> float | None:
    if not isinstance(message, dict):
        return None
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None
    cost = usage.get("cost")
    if isinstance(cost, dict):
        total = cost.get("total")
        if isinstance(total, int | float):
            return float(total)
    total = usage.get("total_cost_usd")
    if isinstance(total, int | float):
        return float(total)
    return None


def _usage_model(message: Any) -> str | None:
    if not isinstance(message, dict):
        return None
    model = message.get("model") or message.get("responseModel")
    return model if isinstance(model, str) and model else None


def _aggregate_usage(messages: Any) -> tuple[float | None, int | None, str | None]:
    """Sum cost/turns across assistant messages in an agent_end transcript.

    pi reports per-message ``usage.cost.total``; we sum across assistant turns
    for the run total. Turn count = number of assistant messages observed.
    """
    if not isinstance(messages, list):
        return None, None, None
    total_cost: float | None = None
    turns = 0
    model: str | None = None
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        turns += 1
        if model is None:
            model = _usage_model(message)
        cost = _usage_cost(message)
        if cost is not None:
            total_cost = cost if total_cost is None else total_cost + cost
    return total_cost, turns or None, model


def _text_from_row(row: dict[str, Any]) -> str:
    for value in _walk_values(row):
        if isinstance(value, dict):
            value_type = value.get("type")
            if value_type in {"text", "message", "content"} and isinstance(value.get("text"), str):
                return value["text"].strip()
    for key in ("text", "message", "content", "result", "output"):
        value = _first_string(row, key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


class PiCodingAgent:
    source = "pi_cli"
    # pi --thinking 的合法取值（off 表示关闭思考）。
    supported_efforts = frozenset({"off", "minimal", "low", "medium", "high", "xhigh"})
    capabilities = CodingAgentCapabilities(
        supports_mcp=False,
        supports_native_json_schema=False,
        supports_skills=False,
        supports_subagents=False,
        supports_cost=True,
        supports_turn_count=True,
        supports_abort=True,
        supports_partial_messages=True,
    )

    def __init__(
        self,
        *,
        backend_key: str = "pi",
        command: str = "pi",
        model: str | None = None,
        provider: str | None = None,
        thinking: str | None = None,
    ) -> None:
        self.backend_key = backend_key
        self.display_name = "Pi"
        self.command = command
        self.model = model
        self.provider = provider
        self.thinking = thinking

    def start(self, request: CodingAgentRequest) -> CodingAgentRun:
        return _PiRun(
            request=request,
            command=self.command,
            model=self.model,
            provider=self.provider,
            thinking=self.thinking,
        )
