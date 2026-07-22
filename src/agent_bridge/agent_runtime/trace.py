"""Agent run tool traces, payload externalisation and timing helpers.

The adapter event stream is intentionally small and backend-neutral. This
module adds cross-backend details needed by the run timeline:

* correlate tool start/result events and measure elapsed time;
* keep short payloads inline;
* write large payloads below the run directory and expose a safe relative ref;
* emit stage timing records using a monotonic clock.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_bridge.agent_runtime.events import event_record, json_safe
from agent_bridge.core.timeutil import utc_iso

INLINE_PAYLOAD_BYTES = 8 * 1024
PREVIEW_CHARS = 2 * 1024
PAYLOAD_DIRNAME = "payloads"
_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9_.-]+")


def _payload_text(value: Any) -> tuple[bytes, str, str]:
    """Return serialized bytes, media type and a readable preview source."""
    safe = json_safe(value)
    if isinstance(safe, str):
        data = safe.encode("utf-8")
        return data, "text/plain; charset=utf-8", safe
    text = json.dumps(safe, ensure_ascii=False, indent=2, default=str)
    return text.encode("utf-8"), "application/json; charset=utf-8", text


def _preview(text: str) -> str:
    if len(text) <= PREVIEW_CHARS:
        return text
    return text[:PREVIEW_CHARS] + "\n…（内容较长，点击查看完整内容）"


def _safe_segment(value: str) -> str:
    cleaned = _SAFE_SEGMENT.sub("-", value).strip("-")
    return cleaned[:120] or "event"


def externalize_event_payloads(
    event: dict[str, Any],
    work_dir: Path | None,
) -> dict[str, Any]:
    """Add payload metadata and externalise content above the inline limit.

    The event keeps the original input/output value when it is small. For
    large values the full content is written to run_dir/payloads and the event
    contains only preview, size and payload reference fields.
    """
    normalized = dict(event)
    for side in ("input", "output"):
        if side not in normalized:
            continue
        value = normalized[side]
        data, content_type, text = _payload_text(value)
        normalized[f"{side}_bytes"] = len(data)
        normalized[f"{side}_content_type"] = content_type
        normalized[f"{side}_preview"] = _preview(text)
        normalized[f"{side}_sha256"] = hashlib.sha256(data).hexdigest()
        if len(data) <= INLINE_PAYLOAD_BYTES:
            continue

        if work_dir is None:
            normalized[side] = _preview(text)
            normalized[f"{side}_truncated"] = True
            normalized[f"{side}_storage_status"] = "not_persisted"
            continue

        call_id = str(
            normalized.get("call_id")
            or normalized.get("tool_use_id")
            or normalized.get("event_id")
            or "event"
        )
        suffix = ".txt" if content_type.startswith("text/") else ".json"
        digest = str(normalized[f"{side}_sha256"])[:12]
        filename = f"{_safe_segment(call_id)}-{side}-{digest}{suffix}"
        relative_ref = f"{PAYLOAD_DIRNAME}/{filename}"
        payload_path = work_dir / relative_ref
        try:
            payload_path.parent.mkdir(parents=True, exist_ok=True)
            payload_path.write_bytes(data)
        except OSError:
            normalized[side] = _preview(text)
            normalized[f"{side}_truncated"] = True
            normalized[f"{side}_storage_status"] = "write_failed"
            continue

        normalized.pop(side, None)
        normalized[f"{side}_payload_ref"] = relative_ref
        normalized[f"{side}_truncated"] = True
        normalized[f"{side}_storage_status"] = "run_file"
    return normalized


def read_payload(work_dir: Path, relative_ref: str) -> tuple[bytes, str]:
    """Read a payload ref while preventing traversal outside the run directory."""
    ref = Path(relative_ref)
    if (
        ref.is_absolute()
        or not relative_ref
        or relative_ref.startswith("../")
        or not ref.parts
        or ref.parts[0] != PAYLOAD_DIRNAME
    ):
        raise ValueError("invalid payload reference")
    root = work_dir.resolve()
    payload_path = (work_dir / ref).resolve()
    if payload_path != root and root not in payload_path.parents:
        raise ValueError("payload reference escapes run directory")
    if not payload_path.is_file():
        raise FileNotFoundError(relative_ref)
    media_type = "text/plain; charset=utf-8"
    if payload_path.suffix.lower() == ".json":
        media_type = "application/json; charset=utf-8"
    return payload_path.read_bytes(), media_type


@dataclass
class _OpenToolCall:
    started_at: str
    started_monotonic: float


@dataclass
class ToolTimingTracker:
    """Correlate tool start/result events within one agent run."""

    _open: dict[str, _OpenToolCall] = field(default_factory=dict)

    def apply(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for original in events:
            event = dict(original)
            kind = event.get("kind")
            tool_id = str(event.get("tool_use_id") or event.get("call_id") or "")
            if kind in {"tool_call", "tool_result"} and tool_id:
                event["call_id"] = tool_id
            if kind == "tool_call" and event.get("status") == "started" and tool_id:
                started_at = str(event.get("created_at") or utc_iso())
                self._open[tool_id] = _OpenToolCall(started_at, time.monotonic())
                event["started_at"] = started_at
            elif kind == "tool_result" and tool_id:
                finished_at = str(event.get("created_at") or utc_iso())
                event["finished_at"] = finished_at
                opened = self._open.pop(tool_id, None)
                if opened is not None:
                    event["started_at"] = opened.started_at
                    event["duration_ms"] = max(
                        0, int((time.monotonic() - opened.started_monotonic) * 1000)
                    )
                else:
                    event["duration_status"] = "unavailable"
            normalized.append(event)
        return normalized

    def close_open(self) -> list[dict[str, Any]]:
        """Close calls that never emitted a result as unknown."""
        now = time.monotonic()
        finished_at = utc_iso()
        events: list[dict[str, Any]] = []
        for tool_id, opened in list(self._open.items()):
            events.append(
                event_record(
                    "tool_result",
                    status="unknown",
                    call_id=tool_id,
                    tool_use_id=tool_id,
                    message="工具调用未收到结束事件",
                    started_at=opened.started_at,
                    finished_at=finished_at,
                    duration_ms=max(0, int((now - opened.started_monotonic) * 1000)),
                    duration_status="closed_without_result",
                )
            )
        self._open.clear()
        return events


@dataclass
class StageTimer:
    """Monotonic timer that produces one completed stage event."""

    name: str
    agent_name: str = "agent"
    source: str = "agent_runtime"
    started_at: str = field(default_factory=utc_iso)
    _started_monotonic: float = field(default_factory=time.monotonic)

    def finish(self, *, status: str = "success", message: str | None = None) -> dict[str, Any]:
        finished_at = utc_iso()
        values: dict[str, Any] = {
            "stage_name": self.name,
            "status": status,
            "started_at": self.started_at,
            "finished_at": finished_at,
            "duration_ms": max(0, int((time.monotonic() - self._started_monotonic) * 1000)),
        }
        if message:
            values["message"] = message
        return event_record(
            "stage",
            agent_name=self.agent_name,
            source=self.source,
            **values,
        )
