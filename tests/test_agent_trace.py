from __future__ import annotations

import json

from agent_bridge.agent_runtime.trace import (
    INLINE_PAYLOAD_BYTES,
    StageTimer,
    ToolTimingTracker,
    externalize_event_payloads,
    read_payload,
)


def test_large_tool_payload_is_written_and_read_by_relative_ref(tmp_path) -> None:
    value = {"output": "x" * (INLINE_PAYLOAD_BYTES + 1)}
    event = externalize_event_payloads(
        {
            "kind": "tool_result",
            "call_id": "call/1",
            "output": value,
        },
        tmp_path,
    )

    assert "output" not in event
    assert event["output_truncated"] is True
    assert event["output_storage_status"] == "run_file"
    assert event["output_payload_ref"].startswith("payloads/")
    payload, media_type = read_payload(tmp_path, event["output_payload_ref"])
    assert media_type.startswith("application/json")
    assert json.loads(payload) == value


def test_payload_ref_cannot_escape_run_directory(tmp_path) -> None:
    try:
        read_payload(tmp_path, "../outside.json")
    except ValueError:
        pass
    else:
        raise AssertionError("path traversal ref should be rejected")

    try:
        read_payload(tmp_path, "messages.jsonl")
    except ValueError:
        pass
    else:
        raise AssertionError("non-payload run file should not be exposed")


def test_tool_timing_tracker_correlates_call_and_result() -> None:
    tracker = ToolTimingTracker()
    call = tracker.apply(
        [{"kind": "tool_call", "status": "started", "tool_use_id": "call_1"}]
    )[0]
    result = tracker.apply(
        [{"kind": "tool_result", "status": "success", "tool_use_id": "call_1"}]
    )[0]

    assert call["call_id"] == "call_1"
    assert call["started_at"]
    assert result["call_id"] == "call_1"
    assert result["started_at"]
    assert result["finished_at"]
    assert result["duration_ms"] >= 0


def test_tool_timing_tracker_preserves_provider_duration() -> None:
    tracker = ToolTimingTracker()
    normalized = tracker.apply(
        [
            {"kind": "tool_call", "status": "started", "tool_use_id": "call_1"},
            {
                "kind": "tool_result",
                "status": "success",
                "tool_use_id": "call_1",
                "duration_ms": 123,
                "duration_status": "provider",
            },
        ]
    )

    assert normalized[1]["duration_ms"] == 123
    assert normalized[1]["duration_status"] == "provider"
    assert tracker.close_open() == []


def test_stage_timer_emits_elapsed_stage_record() -> None:
    event = StageTimer("backend.run", agent_name="codex").finish()

    assert event["kind"] == "stage"
    assert event["agent_name"] == "codex"
    assert event["source"] == "agent_runtime"
    assert event["stage_name"] == "backend.run"
    assert event["started_at"]
    assert event["finished_at"]
    assert event["duration_ms"] >= 0
