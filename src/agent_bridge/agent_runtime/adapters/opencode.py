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
class _OpenCodeRun:
    request: CodingAgentRequest
    command: str
    model: str | None = None
    auto_approve: bool = True
    _process: asyncio.subprocess.Process | None = field(default=None, init=False)

    async def updates(self) -> AsyncIterator[CodingAgentUpdate]:
        prompt = _effective_prompt(self.request)
        args = _build_command(
            command=self.command,
            prompt=prompt,
            cwd=str(self.request.cwd),
            model=self.request.model or self.model,
            auto_approve=self.auto_approve,
        )
        process = await asyncio.create_subprocess_exec(
            *args,
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
                events, maybe_text, maybe_final = _events_from_opencode_row(raw)
                if maybe_text:
                    final_text_parts.append(maybe_text)
                if maybe_final is not None:
                    final = maybe_final
                yield CodingAgentUpdate(raw=raw, events=events, final=maybe_final)
            return_code = await process.wait()
            await stderr_task
            if return_code != 0:
                message = _stderr_summary(stderr_chunks) or f"opencode exited with status {return_code}"
                yield CodingAgentUpdate(
                    events=[_opencode_event("error", status="failed", message=message)],
                    final=CodingAgentFinal(is_error=True, result=message),
                )
                return
            if final is None:
                yield CodingAgentUpdate(
                    final=CodingAgentFinal(result="\n".join(final_text_parts).strip())
                )
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


def _decode_json_line(line: str) -> dict[str, Any]:
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return {"type": "stdout", "message": line}
    return value if isinstance(value, dict) else {"type": "stdout", "value": value}


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


def _walk_values(value: Any) -> list[Any]:
    values = [value]
    if isinstance(value, dict):
        for item in value.values():
            values.extend(_walk_values(item))
    elif isinstance(value, list):
        for item in value:
            values.extend(_walk_values(item))
    return values


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


def _first_number(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        if isinstance(value, int | float):
            return float(value)
    return None


def _first_int(row: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = row.get(key)
        if isinstance(value, int):
            return value
    return None


class OpenCodeCodingAgent:
    source = "opencode_cli"
    capabilities = CodingAgentCapabilities(
        supports_mcp=False,
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
