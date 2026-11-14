"""Coding Agent 统一事件格式；历史 transcript 解析独立保留。"""
from __future__ import annotations

import json
from typing import Any, TextIO

from agent_bridge.core.timeutil import utc_iso

def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list | tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    return str(value)


def event_record(
    kind: str,
    *,
    agent_name: str = "agent",
    source: str = "agent_runtime",
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
