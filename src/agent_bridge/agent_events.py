"""Shared Claude Agent SDK message → event extraction.

Used by both :class:`AgentService` (to persist a canonical event stream per
run) and the workflow runner (to write ``events.jsonl``), so every agent run
records identical event semantics regardless of caller.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, TextIO

import json

_NOISY_PARTIAL_SUBTYPES = {"thinking_tokens", "task_progress"}


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list | tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    return str(value)


def message_log_record(message: Any) -> dict[str, Any]:
    """Raw-ish log record for one SDK message (type + useful attrs + content)."""
    record: dict[str, Any] = {"type": type(message).__name__}
    for attr in ("subtype", "session_id", "uuid", "result", "total_cost_usd", "duration_ms", "num_turns"):
        if hasattr(message, attr):
            record[attr] = json_safe(getattr(message, attr))
    if hasattr(message, "content"):
        record["content"] = json_safe(getattr(message, "content"))
    return record


def event_record(kind: str, **values: Any) -> dict[str, Any]:
    return {
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "agent_name": "claude",
        "source": "claude_agent_sdk",
        "kind": kind,
        **{key: json_safe(value) for key, value in values.items() if value is not None},
    }


def write_event(events: TextIO, record: dict[str, Any]) -> None:
    events.write(json.dumps(record, ensure_ascii=False) + "\n")
    events.flush()


def is_noisy_partial_message(message: Any) -> bool:
    """High-frequency SDK partials (thinking_tokens, task_progress) — drop from logs."""
    return getattr(message, "subtype", None) in _NOISY_PARTIAL_SUBTYPES


def message_events(message: Any, tool_names: dict[str, str]) -> list[dict[str, Any]]:
    """Project one SDK message into semantic events (status/agent_message/tool_call/tool_result/result)."""
    session_id = getattr(message, "session_id", None)
    message_type = type(message).__name__
    if message_type == "ResultMessage":
        status = "failed" if getattr(message, "is_error", False) else "success"
        result = getattr(message, "result", None) or getattr(message, "subtype", "")
        return [
            event_record(
                "result",
                status=status,
                message=result,
                session_id=session_id,
                total_cost_usd=getattr(message, "total_cost_usd", None),
                num_turns=getattr(message, "num_turns", None),
            )
        ]

    records: list[dict[str, Any]] = []
    content = getattr(message, "content", None)
    if isinstance(content, list):
        for block in content:
            block_type = type(block).__name__
            if block_type == "TextBlock":
                text = str(getattr(block, "text", "")).strip()
                if text:
                    records.append(event_record("agent_message", message=text, session_id=session_id))
            elif block_type in {"ToolUseBlock", "ServerToolUseBlock"}:
                tool_id = str(getattr(block, "id", "") or getattr(block, "tool_use_id", ""))
                tool_name = str(getattr(block, "name", "") or "unknown")
                if tool_id:
                    tool_names[tool_id] = tool_name
                records.append(
                    event_record(
                        "tool_call",
                        status="started",
                        tool_name=tool_name,
                        tool_use_id=tool_id,
                        message=f"调用工具 {tool_name}",
                        session_id=session_id,
                    )
                )
            elif block_type in {"ToolResultBlock", "ServerToolResultBlock"}:
                tool_id = str(getattr(block, "tool_use_id", "") or "")
                tool_name = tool_names.get(tool_id, tool_id or "unknown")
                status = "failed" if getattr(block, "is_error", False) else "success"
                records.append(
                    event_record(
                        "tool_result",
                        status=status,
                        tool_name=tool_name,
                        tool_use_id=tool_id,
                        message=f"工具 {tool_name} 调用{'失败' if status == 'failed' else '成功'}",
                        session_id=session_id,
                    )
                )
    elif isinstance(content, str) and content.strip():
        records.append(event_record("agent_message", message=content.strip(), session_id=session_id))

    if records:
        return records

    subtype = getattr(message, "subtype", None)
    if is_noisy_partial_message(message):
        return []
    if subtype:
        return [
            event_record(
                "status",
                status=str(subtype),
                message=str(subtype),
                session_id=session_id,
            )
        ]
    return []
