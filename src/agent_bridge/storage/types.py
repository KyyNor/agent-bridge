from __future__ import annotations

import json
import sqlite3
from typing import Any


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def json_bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


def json_summary(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {"keys": sorted(str(key) for key in value), "bytes": json_bytes(value)}
    if isinstance(value, list):
        return {"items": len(value), "bytes": json_bytes(value)}
    return {"type": type(value).__name__, "bytes": json_bytes(value)}
