"""Time-ordered identifiers for sortable run directories."""

from __future__ import annotations

import os
import re
import time
import uuid

_PREFIX_SANITIZE = re.compile(r"[^A-Za-z0-9_-]+")


def uuid7() -> uuid.UUID:
    """Generate a time-ordered UUID v7 (RFC 9562).

    Python 3.11 stdlib has no ``uuid7``, so this constructs one manually:
    the high 48 bits encode the Unix millisecond timestamp, which makes the
    ``.hex`` representation sort lexicographically in chronological order.
    """
    timestamp_ms = time.time_ns() // 1_000_000
    rand_a = int.from_bytes(os.urandom(2), "big") & 0x0FFF
    rand_b = int.from_bytes(os.urandom(8), "big") & 0x3FFFFFFFFFFFFFFF
    high = (timestamp_ms << 16) | (0x7 << 12) | rand_a
    low = (0b10 << 62) | rand_b
    return uuid.UUID(int=(high << 64) | low)


def new_run_id(prefix: str) -> str:
    """Build a sortable run id ``{prefix}_{uuid7hex}``.

    The prefix is sanitized to path-safe ``[A-Za-z0-9_-]`` characters (unsafe
    runs collapse to ``-``) and the hex suffix encodes the creation timestamp,
    so directory listings sort chronologically by name.
    """
    safe = _PREFIX_SANITIZE.sub("-", (prefix or "").strip()).strip("-_") or "run"
    return f"{safe}_{uuid7().hex}"

