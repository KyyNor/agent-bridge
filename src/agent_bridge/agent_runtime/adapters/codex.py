from __future__ import annotations

import asyncio
import json
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
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
class _CodexRun:
    request: CodingAgentRequest
    command: str
    model: str | None = None
    bypass_approvals_and_sandbox: bool = True
    _process: asyncio.subprocess.Process | None = field(default=None, init=False)

    async def updates(self) -> AsyncIterator[CodingAgentUpdate]:
        schema_path = _write_schema_file(self.request.output_schema)
        args = _build_command(
            command=self.command,
            prompt=_effective_prompt(self.request),
            cwd=str(self.request.cwd),
            model=self.request.model or self.model,
            schema_path=schema_path,
            bypass_approvals_and_sandbox=self.bypass_approvals_and_sandbox,
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
                if self.request.on_native_message is not None:
                    self.request.on_native_message(raw)
                events, maybe_text, maybe_final = _events_from_codex_row(raw)
                if maybe_text:
                    final_text_parts.append(maybe_text)
                if maybe_final is not None:
                    final = maybe_final
                yield CodingAgentUpdate(raw=raw, events=events, final=maybe_final)
            return_code = await process.wait()
            await stderr_task
            if return_code != 0:
                message = _stderr_summary(stderr_chunks) or f"codex exited with status {return_code}"
                yield CodingAgentUpdate(
                    events=[_codex_event("error", status="failed", message=message)],
                    final=CodingAgentFinal(is_error=True, result=message),
                )
                return
            if final is None:
                yield CodingAgentUpdate(
                    final=CodingAgentFinal(result="\n".join(final_text_parts).strip())
                )
            elif self.request.output_schema and _extract_json(final.result or "") is None:
                text = "\n".join(part.strip() for part in final_text_parts if part.strip()).strip()
                if _extract_json(text) is not None:
                    yield CodingAgentUpdate(
                        final=CodingAgentFinal(
                            result=text,
                            structured_output=final.structured_output,
                            subtype=final.subtype,
                            session_id=final.session_id,
                            cost_usd=final.cost_usd,
                            num_turns=final.num_turns,
                            model=final.model,
                        )
                    )
        finally:
            if not stderr_task.done():
                stderr_task.cancel()
            if schema_path:
                try:
                    Path(schema_path).unlink(missing_ok=True)
                except OSError:
                    pass

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


def _write_schema_file(schema: dict[str, Any] | None) -> str | None:
    if not schema:
        return None
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as fh:
        json.dump(schema, fh, ensure_ascii=False)
        return fh.name


def _build_command(
    *,
    command: str,
    prompt: str,
    cwd: str,
    model: str | None,
    schema_path: str | None,
    bypass_approvals_and_sandbox: bool = True,
) -> list[str]:
    args = [command, "exec", "--json", "--cd", cwd, "--skip-git-repo-check"]
    if model:
        args.extend(["--model", model])
    if bypass_approvals_and_sandbox:
        args.append("--dangerously-bypass-approvals-and-sandbox")
    if schema_path:
        args.extend(["--output-schema", schema_path])
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


def _events_from_codex_row(
    row: dict[str, Any],
) -> tuple[list[dict[str, Any]], str | None, CodingAgentFinal | None]:
    row_type = str(row.get("type") or row.get("event") or row.get("kind") or "").lower()
    session_id = _first_string(row, "session_id", "sessionId", "sessionID", "id")
    status = str(row.get("status") or row.get("state") or "").lower()

    if row_type in {"agent_message", "message", "text", "content", "stdout"}:
        text = _text_from_row(row)
        if not text:
            return [], None, None
        return [
            _codex_event("agent_message", agent_role="main", message=text, session_id=session_id)
        ], text, None

    if row_type in {"tool_call", "tool_result", "exec_command", "command"}:
        tool_name = _tool_name(row)
        tool_use_id = _first_string(row, "tool_use_id", "toolUseID", "call_id", "id")
        failed = _is_error(row)
        event_kind = "tool_result" if row_type == "tool_result" or status in {"completed", "success", "error", "failed"} else "tool_call"
        return [
            _codex_event(
                event_kind,
                agent_role="main",
                status="failed" if failed else ("success" if event_kind == "tool_result" else "started"),
                tool_name=tool_name,
                tool_use_id=tool_use_id,
                message=f"工具 {tool_name} 调用{'失败' if failed else ('成功' if event_kind == 'tool_result' else '')}".strip(),
                session_id=session_id,
            )
        ], None, None

    if row_type in {"result", "completed", "complete", "done", "task_complete"}:
        message = _text_from_row(row) or str(row.get("result") or row.get("message") or "done")
        failed = _is_error(row)
        final = CodingAgentFinal(
            is_error=failed,
            result=message,
            structured_output=row.get("structured_output"),
            session_id=session_id,
            cost_usd=_first_number(row, "cost", "cost_usd", "total_cost_usd"),
            num_turns=_first_int(row, "turns", "num_turns"),
            model=_first_string(row, "model"),
        )
        return [
            _codex_event(
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
            _codex_event("error", status="failed", message=message, session_id=session_id)
        ], None, CodingAgentFinal(is_error=True, result=message, session_id=session_id)

    text = _text_from_row(row)
    if text:
        return [
            _codex_event("agent_message", agent_role="main", message=text, session_id=session_id)
        ], text, None
    return [], None, None


def _codex_event(kind: str, **values: Any) -> dict[str, Any]:
    return event_record(kind, agent_name="codex", source="codex_cli", **values)


def _text_from_row(row: dict[str, Any]) -> str:
    for value in _walk_values(row):
        if isinstance(value, dict):
            value_type = value.get("type")
            if value_type in {"text", "message", "content"} and isinstance(value.get("text"), str):
                return value["text"].strip()
    for key in ("message", "text", "content", "result", "output", "last_message"):
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
    for key in ("tool", "tool_name", "toolName", "name", "command"):
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    for value in _walk_values(row):
        if isinstance(value, dict):
            for key in ("tool", "tool_name", "toolName", "name", "command"):
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


class CodexCodingAgent:
    source = "codex_cli"
    capabilities = CodingAgentCapabilities(
        supports_mcp=False,
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
        backend_key: str = "codex",
        command: str = "codex",
        model: str | None = None,
        bypass_approvals_and_sandbox: bool = True,
    ) -> None:
        self.backend_key = backend_key
        self.display_name = "Codex"
        self.command = command
        self.model = model
        self.bypass_approvals_and_sandbox = bypass_approvals_and_sandbox

    def start(self, request: CodingAgentRequest) -> CodingAgentRun:
        return _CodexRun(
            request=request,
            command=self.command,
            model=self.model,
            bypass_approvals_and_sandbox=self.bypass_approvals_and_sandbox,
        )
