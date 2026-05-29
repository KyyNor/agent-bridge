from __future__ import annotations

import re
import unicodedata
from pathlib import Path


def make_slug(value: str) -> str:
    stem = Path(value.strip()).stem
    normalized = unicodedata.normalize("NFKC", stem).lower()
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", "-", normalized, flags=re.UNICODE)
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-_")
    return normalized or "document"


def unique_slug(base: str, existing: set[str]) -> str:
    if base not in existing:
        return base
    index = 2
    while f"{base}-{index}" in existing:
        index += 1
    return f"{base}-{index}"
