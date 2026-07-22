from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from typing import Any

from agent_bridge.agent_runtime.adapters.jsonl_cli import (
    JsonlCliProcess,
    effective_prompt as _effective_prompt,
    extract_json_object as _extract_json,
    first_int as _first_int,
    first_number as _first_number,
    first_string as _first_string,
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
class _OpenCodeRun:
    request: CodingAgentRequest
    command: str
    model: str | None = None
    auto_approve: bool = True
    _cli: JsonlCliProcess | None = field(default=None, init=False)

    async def updates(self) -> AsyncIterator[CodingAgentUpdate]:
        prompt = _effective_prompt(self.request)
        args = _build_command(
            command=self.command,
            prompt=prompt,
            cwd=str(self.request.cwd),
            model=self.request.model or self.model,
            auto_approve=self.auto_approve,
        )
        cli = JsonlCliProcess(request=self.request, args=args)
        self._cli = cli
        await cli.start()
        final_text_parts: list[str] = []
        final: CodingAgentFinal | None = None
        try:
            async for raw in cli.rows():
                events, maybe_text, maybe_final = _events_from_opencode_row(raw)
                if maybe_text:
                    final_text_parts.append(maybe_text)
                if maybe_final is not None:
                    final = maybe_final
                yield CodingAgentUpdate(raw=raw, events=events, final=maybe_final)
            return_code = await cli.wait()
            if return_code != 0:
                message = cli.stderr_summary() or f"opencode exited with status {return_code}"
                yield CodingAgentUpdate(
                    events=[_opencode_event("error", status="failed", message=message)],
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
    cwd: str,
    model: str | None,
    auto_approve: bool,
) -> list[str]:
    args = [command, "run", "--format", "json", "--dir", cwd]
    if model:
        args.extend(["--model", model])
    if auto_approve:
        args.append("--auto")
    args.append(prompt)
    return args


def _completed_final_from_text(
    final: CodingAgentFinal,
    final_text_parts: list[str],
    request: CodingAgentRequest,
) -> CodingAgentFinal:
    """Prefer the assistant's final text over OpenCode's generic status final.

    ``opencode run --format json`` may emit a terminal row such as
    ``{"type":"result","result":"done"}`` after the actual assistant answer has
    already streamed as a text event. For schema-based runs that loses the JSON
    object the UI needs, so normalize the final value once the process ends.
    """
    text = _joined_text(final_text_parts)
    if not text or final.is_error:
        return final
    final_result = str(final.result or "").strip()
    if _is_generic_final_result(final_result):
        return replace(final, result=text)
    if request.output_schema and _extract_json(final_result) is None and _extract_json(text) is not None:
        return replace(final, result=text)
    return final


def _events_from_opencode_row(
    row: dict[str, Any],
) -> tuple[list[dict[str, Any]], str | None, CodingAgentFinal | None]:
    row_type = str(row.get("type") or row.get("event") or row.get("kind") or "")
    session_id = _first_string(row, "sessionID", "session_id", "sessionId", "id")
    status = str(row.get("status") or row.get("state") or "")

    if row_type in {"text", "message", "message.part.updated", "content"}:
        text = _text_from_row(row)
        if not text:
            return [], None, None
        return [
            _opencode_event(
                "agent_message",
                agent_role="main",
                message=text,
                session_id=session_id,
            )
        ], text, None

    if row_type in {"tool", "tool_use", "tool.call", "tool_call", "tool_result", "tool.result"}:
        tool_name = _tool_name(row)
        tool_use_id = _first_string(row, "id", "toolUseID", "tool_use_id", "callID")
        failed = _is_error(row)
        events = [
            _opencode_event(
                "tool_call",
                agent_role="main",
                status="started",
                tool_name=tool_name,
                tool_use_id=tool_use_id,
                message=f"调用工具 {tool_name}",
                session_id=session_id,
            )
        ]
        if row_type in {"tool_result", "tool.result"} or status in {"completed", "success", "error", "failed"}:
            result_status = "failed" if failed else "success"
            events.append(
                _opencode_event(
                    "tool_result",
                    agent_role="main",
                    status=result_status,
                    tool_name=tool_name,
                    tool_use_id=tool_use_id,
                    message=f"工具 {tool_name} 调用{'失败' if result_status == 'failed' else '成功'}",
                    session_id=session_id,
                )
            )
        return events, None, None

    if row_type in {"step_start", "step.started", "status"} or status:
        message = status or row_type
        return [
            _opencode_event(
                "status",
                agent_role="main",
                status=message,
                message=message,
                session_id=session_id,
            )
        ], None, None

    if row_type in {"step_finish", "step.finished", "result", "done"}:
        message = _text_from_row(row) or str(row.get("reason") or "done")
        failed = _is_error(row)
        final = CodingAgentFinal(
            is_error=failed,
            result=message,
            session_id=session_id,
            cost_usd=_first_number(row, "cost", "cost_usd", "total_cost_usd"),
            num_turns=_first_int(row, "turns", "num_turns"),
        )
        return [
            _opencode_event(
                "result",
                agent_role="main",
                status="failed" if failed else "success",
                message=message,
                session_id=session_id,
                total_cost_usd=final.cost_usd,
                num_turns=final.num_turns,
            )
        ], None, final

    if row_type in {"error", "failed"} or _is_error(row):
        message = _text_from_row(row) or str(row.get("error") or row.get("message") or row_type)
        return [
            _opencode_event("error", status="failed", message=message, session_id=session_id)
        ], None, CodingAgentFinal(is_error=True, result=message, session_id=session_id)

    text = _text_from_row(row)
    if text:
        return [
            _opencode_event(
                "agent_message",
                agent_role="main",
                message=text,
                session_id=session_id,
            )
        ], text, None
    return [], None, None


def _opencode_event(kind: str, **values: Any) -> dict[str, Any]:
    return event_record(kind, agent_name="opencode", source="opencode_cli", **values)


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


def _tool_name(row: dict[str, Any]) -> str:
    for key in ("tool", "tool_name", "toolName", "name"):
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    for value in _walk_values(row):
        if isinstance(value, dict):
            for key in ("tool", "tool_name", "toolName", "name"):
                item = value.get(key)
                if isinstance(item, str) and item:
                    return item
    return "unknown"


class OpenCodeCodingAgent:
    source = "opencode_cli"
    capabilities = CodingAgentCapabilities(
        supports_mcp=True,
        supports_native_json_schema=False,
        supports_skills=False,
        supports_subagents=False,
        supports_cost=True,
        supports_turn_count=False,
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
