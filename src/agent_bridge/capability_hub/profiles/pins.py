"""Helpers for computing profile tool pin previews."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any

from agent_bridge.capability_hub.models import ToolType


PINNABLE_TOOL_TYPES = {
    ToolType.overview.value,
    ToolType.search.value,
    ToolType.detail.value,
}
PIN_TOOL_PREFIX = "pin_"


@dataclass(frozen=True)
class PinnedGroup:
    service_key: str
    tool_type: str
    source: str


def _safe_name_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", value).strip("_")


def safe_pin_tool_name(service_key: str, tool_name: str) -> str:
    safe_service = _safe_name_part(service_key.lower())
    safe_tool = _safe_name_part(tool_name)
    return f"{PIN_TOOL_PREFIX}{safe_service}_{safe_tool}"


def ratio_target(candidate_count: int, ratio_percent: int) -> int:
    if candidate_count <= 0 or ratio_percent <= 0:
        return 0
    return math.ceil(candidate_count * ratio_percent / 100)


def _json_loads(value: Any, default: Any) -> Any:
    if not isinstance(value, str):
        return value if value is not None else default
    try:
        return json.loads(value) if value else default
    except json.JSONDecodeError:
        return default


def tool_payload_to_pin_tool(
    *,
    tool: dict[str, Any],
    service_name: str,
    source: str,
) -> dict[str, Any]:
    return {
        "generated_tool_name": safe_pin_tool_name(tool["service_key"], tool["tool_name"]),
        "service_key": tool["service_key"],
        "service_name": service_name,
        "tool_name": tool["tool_name"],
        "tool_type": tool["tool_type"],
        "source": source,
        "description": tool.get("description") or "",
        "input_schema": _json_loads(tool.get("input_schema_json"), {}),
    }
