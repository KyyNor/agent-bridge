"""Subagent attribution in event extraction (feature 5).

The Claude Agent SDK emits Task* lifecycle messages (task_started /
task_progress / task_notification / task_updated) when the main agent spawns a
sub-agent via the Task tool, and tags the sub-agent's own assistant/user
messages with ``parent_tool_use_id``. These tests pin the projection of those
messages into the canonical event stream used by both AgentService and the
workflow runner.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from agent_bridge.agent_runtime.events import message_events, Attribution


def _block(type_name: str, **kw) -> Any:
    """Build a content block stand-in whose ``type().__name__`` matches the SDK
    block class name (TextBlock / ToolUseBlock / ...)."""
    cls = type(type_name, (SimpleNamespace,), {})
    return cls(**kw)


def _msg(type_name: str, content=None, **kw) -> Any:
    """Build a minimal stand-in for an SDK message dataclass by type name.

    Creates a real (dynamically-named) class so ``type(obj).__name__`` matches
    the SDK dataclass name the extractor switches on.
    """
    attrs: dict[str, Any] = {"content": content}
    attrs.update(kw)
    cls = type(type_name, (SimpleNamespace,), {})
    return cls(**attrs)


# ---------------------------------------------------------------------------
# Task lifecycle messages → subagent_* events
# ---------------------------------------------------------------------------

def test_task_started_produces_subagent_start_event():
    msg = _msg(
        "TaskStartedMessage",
        content=None,
        subtype="task_started",
        task_id="task_1",
        description="search the codebase",
        uuid="u1",
        session_id="s1",
        tool_use_id="tu_1",
        task_type="subagent",
    )
    events = message_events(msg, {})
    assert len(events) == 1
    ev = events[0]
    assert ev["kind"] == "subagent_start"
    assert ev["task_id"] == "task_1"
    assert ev["description"] == "search the codebase"
    assert ev["tool_use_id"] == "tu_1"
    assert ev["agent_role"] == "subagent"


def test_task_notification_produces_subagent_end_event():
    msg = _msg(
        "TaskNotificationMessage",
        content=None,
        subtype="task_notification",
        task_id="task_1",
        status="completed",
        output_file="/tmp/out.json",
        summary="found 3 files",
        uuid="u2",
        session_id="s1",
        tool_use_id="tu_1",
        usage={"total_tokens": 1200, "tool_uses": 4, "duration_ms": 5000},
    )
    events = message_events(msg, {})
    assert len(events) == 1
    ev = events[0]
    assert ev["kind"] == "subagent_end"
    assert ev["task_id"] == "task_1"
    assert ev["status"] == "completed"
    assert ev["summary"] == "found 3 files"
    assert ev["usage"]["total_tokens"] == 1200
    assert ev["usage"]["duration_ms"] == 5000


def test_task_progress_produces_subagent_progress_event():
    msg = _msg(
        "TaskProgressMessage",
        content=None,
        subtype="task_progress",
        task_id="task_1",
        description="search the codebase",
        usage={"total_tokens": 600, "tool_uses": 2, "duration_ms": 2000},
        uuid="u3",
        session_id="s1",
        tool_use_id="tu_1",
        last_tool_name="Grep",
    )
    events = message_events(msg, {})
    assert len(events) == 1
    ev = events[0]
    assert ev["kind"] == "subagent_progress"
    assert ev["task_id"] == "task_1"
    assert ev["last_tool_name"] == "Grep"
    assert ev["usage"]["tool_uses"] == 2


def test_task_updated_produces_subagent_updated_event():
    msg = _msg(
        "TaskUpdatedMessage",
        content=None,
        subtype="task_updated",
        task_id="task_1",
        patch={"status": "completed"},
        status="completed",
        session_id="s1",
        uuid="u4",
    )
    events = message_events(msg, {})
    assert len(events) == 1
    ev = events[0]
    assert ev["kind"] == "subagent_updated"
    assert ev["task_id"] == "task_1"
    assert ev["status"] == "completed"


# ---------------------------------------------------------------------------
# Attribution: parent_tool_use_id → task_id
# ---------------------------------------------------------------------------

def test_attribution_links_task_started_tool_use_id_to_task_id():
    """When a Task starts, the spawning tool_use_id is remembered so later
    messages carrying parent_tool_use_id can be attributed to that task."""
    attr = Attribution()
    started = _msg(
        "TaskStartedMessage",
        content=None,
        subtype="task_started",
        task_id="task_7",
        description="analyse deps",
        uuid="u",
        session_id="s",
        tool_use_id="tu_7",
    )
    message_events(started, {}, attribution=attr)
    assert attr.task_id_for_tool_use("tu_7") == "task_7"
    assert attr.description_for_task("task_7") == "analyse deps"


def test_subagent_assistant_message_is_attributed_via_parent_tool_use_id():
    """An assistant message from a sub-agent carries parent_tool_use_id; its
    tool_call / text events must be tagged agent_role=subagent + task_id."""
    attr = Attribution()
    # Seed: a Task tool call (tu_7) spawned task_7.
    message_events(
        _msg(
            "TaskStartedMessage",
            content=None,
            subtype="task_started",
            task_id="task_7",
            description="analyse deps",
            uuid="u",
            session_id="s",
            tool_use_id="tu_7",
        ),
        {},
        attribution=attr,
    )

    # The sub-agent emits a tool call; its message carries parent_tool_use_id.
    sub_msg = _msg(
        "AssistantMessage",
        content=[_block("ToolUseBlock", id="subcall_1", name="Grep")],
        parent_tool_use_id="tu_7",
        session_id="s2",
    )
    events = message_events(sub_msg, {}, attribution=attr)
    assert len(events) == 1
    ev = events[0]
    assert ev["kind"] == "tool_call"
    assert ev["agent_role"] == "subagent"
    assert ev["task_id"] == "task_7"
    assert ev["parent_tool_use_id"] == "tu_7"


def test_main_agent_message_has_no_subagent_attribution():
    """Messages without parent_tool_use_id stay agent_role=main, no task_id."""
    attr = Attribution()
    msg = _msg(
        "AssistantMessage",
        content=[_block("TextBlock", text="hello")],
        parent_tool_use_id=None,
        session_id="s1",
    )
    events = message_events(msg, {}, attribution=attr)
    assert len(events) == 1
    assert events[0]["agent_role"] == "main"
    assert "task_id" not in events[0]


def test_message_events_works_without_attribution_backward_compat():
    """Existing callers pass no attribution; behaviour must be unchanged."""
    msg = _msg(
        "AssistantMessage",
        content=[_block("TextBlock", text="hi")],
        parent_tool_use_id=None,
        session_id="s1",
    )
    events = message_events(msg, {})
    assert events[0]["agent_role"] == "main"
    # No task_id key when unattributed.
    assert "task_id" not in events[0]
