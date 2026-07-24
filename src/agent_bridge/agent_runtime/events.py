"""Shared Claude Agent SDK message → event extraction.

Used by both :class:`AgentService` (to persist a canonical event stream per
run) and the workflow runner (to write ``events.jsonl``), so every agent run
records identical event semantics regardless of caller.
"""

from __future__ import annotations

from typing import Any, TextIO

import json

from agent_bridge.core.timeutil import utc_iso

# SDK message type names that carry Task-lifecycle (sub-agent) metadata. We key
# off the type name (not isinstance) so the extraction works even when the SDK
# message classes are mocked/ducked in tests.
_TASK_STARTED = "TaskStartedMessage"
_TASK_PROGRESS = "TaskProgressMessage"
_TASK_NOTIFICATION = "TaskNotificationMessage"
_TASK_UPDATED = "TaskUpdatedMessage"
_TASK_MESSAGE_TYPES = frozenset({_TASK_STARTED, _TASK_PROGRESS, _TASK_NOTIFICATION, _TASK_UPDATED})

# Subtypes whose raw high-frequency *partial* messages are dropped from the
# canonical event stream. A generic ``task_progress`` streaming partial (a
# SystemMessage with this subtype, no task metadata) is noise; the valuable
# sub-agent progress is carried by the typed ``TaskProgressMessage`` which is
# projected to a ``subagent_progress`` event before reaching this check.
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


def event_record(
    kind: str,
    *,
    agent_name: str = "claude",
    source: str = "claude_agent_sdk",
    **values: Any,
) -> dict[str, Any]:
    return {
        "created_at": utc_iso(),
        "agent_name": agent_name,
        "source": source,
        "kind": kind,
        **{key: json_safe(value) for key, value in values.items() if value is not None},
    }


def write_event(events: TextIO, record: dict[str, Any]) -> None:
    events.write(json.dumps(record, ensure_ascii=False) + "\n")
    events.flush()


def is_noisy_partial_message(message: Any) -> bool:
    """High-frequency SDK partials (thinking_tokens, task_progress) — drop.

    Typed Task-lifecycle messages (``TaskProgressMessage`` etc.) carry valuable
    sub-agent metadata and are projected into ``subagent_*`` events, so they are
    explicitly excluded from "noisy" even though their ``subtype`` may be
    ``task_progress``; only the generic streaming partial is dropped.
    """
    if type(message).__name__ in _TASK_MESSAGE_TYPES:
        return False
    return getattr(message, "subtype", None) in _NOISY_PARTIAL_SUBTYPES


class Attribution:
    """Tracks sub-agent attribution across a single agent run.

    The SDK tags a sub-agent's own assistant/user messages with
    ``parent_tool_use_id`` — the id of the Task tool call that spawned it. When
    a Task starts we record ``tool_use_id -> task_id`` (and the task's
    description); later messages carrying ``parent_tool_use_id`` are then
    attributed to that task. Thread-unsafe by design: one instance per run.
    """

    def __init__(self) -> None:
        self._tool_use_to_task: dict[str, str] = {}
        self._task_meta: dict[str, dict[str, Any]] = {}

    def note_task_started(
        self, *, task_id: str, description: str | None, tool_use_id: str | None
    ) -> None:
        if tool_use_id:
            self._tool_use_to_task[tool_use_id] = task_id
        if task_id not in self._task_meta:
            self._task_meta[task_id] = {"description": description or task_id}

    def task_id_for_tool_use(self, tool_use_id: str | None) -> str | None:
        if not tool_use_id:
            return None
        return self._tool_use_to_task.get(tool_use_id)

    def description_for_task(self, task_id: str) -> str | None:
        meta = self._task_meta.get(task_id)
        return meta["description"] if meta else None


def _usage_dict(usage: Any) -> dict[str, Any] | None:
    """Normalise a SDK TaskUsage (TypedDict / Mapping / object) to a plain dict."""
    if usage is None:
        return None
    if isinstance(usage, dict):
        return {k: json_safe(v) for k, v in usage.items()}
    # Object with attributes.
    return {k: json_safe(getattr(usage, k)) for k in ("total_tokens", "tool_uses", "duration_ms") if hasattr(usage, k)}


def _task_lifecycle_events(message: Any, session_id: Any) -> list[dict[str, Any]]:
    """Project a Task* lifecycle message into a subagent_* event (or none)."""
    message_type = type(message).__name__
    task_id = getattr(message, "task_id", None)
    if message_type == _TASK_STARTED:
        return [
            event_record(
                "subagent_start",
                agent_role="subagent",
                task_id=task_id,
                description=getattr(message, "description", None),
                tool_use_id=getattr(message, "tool_use_id", None),
                session_id=session_id,
            )
        ]
    if message_type == _TASK_PROGRESS:
        return [
            event_record(
                "subagent_progress",
                agent_role="subagent",
                task_id=task_id,
                description=getattr(message, "description", None),
                last_tool_name=getattr(message, "last_tool_name", None),
                usage=_usage_dict(getattr(message, "usage", None)),
                session_id=session_id,
            )
        ]
    if message_type == _TASK_NOTIFICATION:
        return [
            event_record(
                "subagent_end",
                agent_role="subagent",
                task_id=task_id,
                status=getattr(message, "status", None),
                summary=getattr(message, "summary", None),
                usage=_usage_dict(getattr(message, "usage", None)),
                tool_use_id=getattr(message, "tool_use_id", None),
                session_id=session_id,
            )
        ]
    if message_type == _TASK_UPDATED:
        return [
            event_record(
                "subagent_updated",
                agent_role="subagent",
                task_id=task_id,
                status=getattr(message, "status", None),
                session_id=session_id,
            )
        ]
    return []


def message_events(
    message: Any,
    tool_names: dict[str, str],
    *,
    attribution: Attribution | None = None,
) -> list[dict[str, Any]]:
    """Project one SDK message into semantic events.

    Event kinds: status / agent_message / tool_call / tool_result / result /
    structured_output, plus subagent_start / subagent_progress / subagent_end /
    subagent_updated for Task-lifecycle (sub-agent) messages. When ``attribution`` is supplied,
    events originating from a sub-agent (identified via ``parent_tool_use_id``)
    are tagged ``agent_role="subagent"`` with the originating ``task_id``;
    main-agent events are tagged ``agent_role="main"``.
    """
    session_id = getattr(message, "session_id", None)
    message_type = type(message).__name__

    # Sub-agent lifecycle messages first.
    if message_type in _TASK_MESSAGE_TYPES:
        events = _task_lifecycle_events(message, session_id)
        if attribution is not None and message_type == _TASK_STARTED:
            attribution.note_task_started(
                task_id=getattr(message, "task_id", ""),
                description=getattr(message, "description", None),
                tool_use_id=getattr(message, "tool_use_id", None),
            )
        return events

    if message_type == "ResultMessage":
        status = "failed" if getattr(message, "is_error", False) else "success"
        result = getattr(message, "result", None) or getattr(message, "subtype", "")
        return [
            event_record(
                "result",
                agent_role="main",
                status=status,
                message=result,
                session_id=session_id,
                total_cost_usd=getattr(message, "total_cost_usd", None),
                num_turns=getattr(message, "num_turns", None),
            )
        ]

    # Resolve sub-agent attribution for this message, if any.
    parent_tool_use_id = getattr(message, "parent_tool_use_id", None)
    task_id = attribution.task_id_for_tool_use(parent_tool_use_id) if attribution else None
    is_subagent = task_id is not None
    agent_role = "subagent" if is_subagent else "main"

    records: list[dict[str, Any]] = []
    content = getattr(message, "content", None)
    if isinstance(content, list):
        for block in content:
            block_type = type(block).__name__
            if block_type == "TextBlock":
                text = str(getattr(block, "text", "")).strip()
                if text:
                    records.append(
                        event_record(
                            "agent_message",
                            agent_role=agent_role,
                            message=text,
                            task_id=task_id,
                            parent_tool_use_id=parent_tool_use_id if is_subagent else None,
                            session_id=session_id,
                        )
                    )
            elif block_type in {"ToolUseBlock", "ServerToolUseBlock"}:
                tool_id = str(getattr(block, "id", "") or getattr(block, "tool_use_id", ""))
                tool_name = str(getattr(block, "name", "") or "unknown")
                if tool_id:
                    tool_names[tool_id] = tool_name
                records.append(
                    event_record(
                        "tool_call",
                        agent_role=agent_role,
                        status="started",
                        tool_name=tool_name,
                        tool_use_id=tool_id,
                        input=getattr(block, "input", None),
                        task_id=task_id,
                        parent_tool_use_id=parent_tool_use_id if is_subagent else None,
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
                        agent_role=agent_role,
                        status=status,
                        tool_name=tool_name,
                        tool_use_id=tool_id,
                        output=getattr(block, "content", None),
                        is_error=bool(getattr(block, "is_error", False)),
                        task_id=task_id,
                        parent_tool_use_id=parent_tool_use_id if is_subagent else None,
                        message=f"工具 {tool_name} 调用{'失败' if status == 'failed' else '成功'}",
                        session_id=session_id,
                    )
                )
    elif isinstance(content, str) and content.strip():
        records.append(
            event_record(
                "agent_message",
                agent_role=agent_role,
                message=content.strip(),
                task_id=task_id,
                parent_tool_use_id=parent_tool_use_id if is_subagent else None,
                session_id=session_id,
            )
        )

    if records:
        return records

    subtype = getattr(message, "subtype", None)
    if is_noisy_partial_message(message):
        return []
    if subtype:
        return [
            event_record(
                "status",
                agent_role=agent_role,
                status=str(subtype),
                message=str(subtype),
                session_id=session_id,
            )
        ]
    return []
