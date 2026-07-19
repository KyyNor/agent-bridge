from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

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
    _process: asyncio.subprocess.Process | None = field(default=None, init=False)

    async def updates(self) -> AsyncIterator[CodingAgentUpdate]:
        prompt = _effective_prompt(self.request)
        args = _build_command(
            command=self.command,
            prompt=prompt,
            model=self.request.model or self.model,
            provider=self.provider,
            thinking=self.thinking,
        )
        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(self.request.cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._process = process
        stderr_chunks: list[str] = []
        stderr_task = asyncio.create_task(_drain_stderr(process, self.request, stderr_chunks))
        final_text_parts: list[str] = []
        final: CodingAgentFinal | None = None
        try:
            assert process.stdout is not None
            async for raw_line in process.stdout:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                raw = _decode_json_line(line)
                events, maybe_text, maybe_final = _events_from_pi_row(raw)
                if maybe_text:
                    final_text_parts.append(maybe_text)
                if maybe_final is not None:
                    final = maybe_final
                yield CodingAgentUpdate(raw=raw, events=events, final=maybe_final)
            return_code = await process.wait()
            await stderr_task
            if return_code != 0:
                message = _stderr_summary(stderr_chunks) or f"pi exited with status {return_code}"
                yield CodingAgentUpdate(
                    events=[_pi_event("error", status="failed", message=message)],
                    final=CodingAgentFinal(is_error=True, result=message),
                )
                return
            if final is None:
                yield CodingAgentUpdate(
                    final=CodingAgentFinal(result="\n".join(final_text_parts).strip())
                )
            else:
                completed_final = _completed_final_from_text(final, final_text_parts, self.request)
                if completed_final is not final:
                    yield CodingAgentUpdate(final=completed_final)
        finally:
            if not stderr_task.done():
                stderr_task.cancel()

    async def abort(self) -> None:
        if self._process is None or self._process.returncode is not None:
            return
        self._process.terminate()
        try:
            await asyncio.wait_for(self._process.wait(), timeout=5)
        except TimeoutError:
            self._process.kill()
            await self._process.wait()


def _effective_prompt(request: CodingAgentRequest) -> str:
    if not request.system_prompt_append:
        return request.prompt
    return f"{request.system_prompt_append}\n\n{request.prompt}"


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


async def _drain_stderr(
    process: asyncio.subprocess.Process,
    request: CodingAgentRequest,
    chunks: list[str],
) -> None:
    if process.stderr is None:
        return
    async for raw_line in process.stderr:
        text = raw_line.decode("utf-8", errors="replace")
        chunks.append(text)
        if request.stderr is not None:
            request.stderr(text)


def _stderr_summary(chunks: list[str], *, limit: int = 2000) -> str:
    text = "".join(chunks).strip()
    if len(text) <= limit:
        return text
    return text[-limit:]


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
    text = "\n".join(part.strip() for part in final_text_parts if part.strip()).strip()
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


def _is_generic_final_result(text: str) -> bool:
    return text.lower() in {"", "done", "success", "succeeded", "complete", "completed", "ok"}


def _extract_json(text: str) -> Any | None:
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _decode_json_line(line: str) -> dict[str, Any]:
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return {"type": "stdout", "message": line}
    return value if isinstance(value, dict) else {"type": "stdout", "value": value}


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
            return [
                _pi_event(
                    "tool_call",
                    agent_role="main",
                    status="started",
                    tool_name=tool_name,
                    tool_use_id=tool_use_id,
                    message=f"调用工具 {tool_name}",
                    session_id=session_id,
                )
            ], None, None
        if row_type == "tool_execution_end":
            is_error = bool(row.get("isError"))
            return [
                _pi_event(
                    "tool_result",
                    agent_role="main",
                    status="failed" if is_error else "success",
                    tool_name=tool_name,
                    tool_use_id=tool_use_id,
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


def _walk_values(value: Any) -> list[Any]:
    values = [value]
    if isinstance(value, dict):
        for item in value.values():
            values.extend(_walk_values(item))
    elif isinstance(value, list):
        for item in value:
            values.extend(_walk_values(item))
    return values


def _is_error(row: dict[str, Any]) -> bool:
    for key in ("is_error", "isError", "error"):
        value = row.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value:
            return True
    status = str(row.get("status") or row.get("state") or "").lower()
    return status in {"error", "failed", "failure"}


def _first_string(row: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    for value in _walk_values(row):
        if isinstance(value, dict):
            for key in keys:
                item = value.get(key)
                if isinstance(item, str) and item:
                    return item
    return None


class PiCodingAgent:
    source = "pi_cli"
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
