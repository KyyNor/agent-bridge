"""Shared JSON (de)serialization helpers."""
from __future__ import annotations

import json
from typing import Any


def json_loads(value: Any, default: Any) -> Any:
    """Parse a JSON string; return ``default`` for ``None``/empty/invalid input."""
    if not isinstance(value, str):
        return value if value is not None else default
    try:
        return json.loads(value) if value else default
    except json.JSONDecodeError:
        return default
