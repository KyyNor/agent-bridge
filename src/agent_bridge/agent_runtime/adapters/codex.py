from __future__ import annotations

import copy
import json
import subprocess
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_bridge.agent_runtime.adapters.jsonl_cli import (
    JsonlCliProcess,
    effective_prompt as _effective_prompt,
    extract_json_object as _extract_json,
    first_int as _first_int,
    first_number as _first_number,
    first_string as _first_string,
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
class _CodexRun:
    request: CodingAgentRequest
    command: str
    model: str | None = None
    bypass_approvals_and_sandbox: bool = True
    _cli: JsonlCliProcess | None = field(default=None, init=False)

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
        cli = JsonlCliProcess(
            request=self.request,
            args=args,
            stdin=subprocess.DEVNULL,
            forward_native_messages=True,
        )
        self._cli = cli
        await cli.start()
        final_text_parts: list[str] = []
        final: CodingAgentFinal | None = None
        try:
            async for raw in cli.rows():
                events, maybe_text, maybe_final = _events_from_codex_row(raw)
                if maybe_text:
                    final_text_parts.append(maybe_text)
                if maybe_final is not None:
                    final = maybe_final
                yield CodingAgentUpdate(raw=raw, events=events, final=maybe_final)
            return_code = await cli.wait()
            if return_code != 0:
                message = (
                    final.result
                    if final is not None and final.is_error and final.result
                    else cli.stderr_summary() or f"codex exited with status {return_code}"
                )
                yield CodingAgentUpdate(
                    events=[_codex_event("error", status="failed", message=message)],
                    final=CodingAgentFinal(is_error=True, result=message),
                )
                return
            if final is None:
                yield CodingAgentUpdate(
                    final=CodingAgentFinal(result=_joined_text(final_text_parts))
                )
            elif self.request.output_schema and _extract_json(final.result or "") is None:
                text = _joined_text(final_text_parts)
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
            await cli.close()
            if schema_path:
                try:
                    Path(schema_path).unlink(missing_ok=True)
                except OSError:
                    pass

    async def abort(self) -> None:
        if self._cli is not None:
            await self._cli.abort()


def _write_schema_file(schema: dict[str, Any] | None) -> str | None:
    if not schema:
        return None
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as fh:
        json.dump(_schema_for_codex(schema), fh, ensure_ascii=False)
        return fh.name


def _schema_for_codex(schema: dict[str, Any]) -> dict[str, Any]:
    """Codex forwards schemas to strict structured output.

    Strict object schemas require every property to be listed in ``required``.
    Claude's SDK accepts optional properties, so normalize on the Codex side
    instead of forcing every Agent Bridge schema author to remember this rule.
    """
    normalized = copy.deepcopy(schema)
    _require_all_object_properties(normalized)
    return normalized


def _require_all_object_properties(value: Any) -> None:
    if isinstance(value, dict):
        properties = value.get("properties")
        if value.get("type") == "object" and isinstance(properties, dict):
            value["required"] = list(properties.keys())
            for child in properties.values():
                _require_all_object_properties(child)
        for key in ("items", "additionalProperties"):
            _require_all_object_properties(value.get(key))
        for key in ("anyOf", "oneOf", "allOf"):
            variants = value.get(key)
            if isinstance(variants, list):
                for item in variants:
                    _require_all_object_properties(item)
    elif isinstance(value, list):
        for item in value:
            _require_all_object_properties(item)


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
