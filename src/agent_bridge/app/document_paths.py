"""Validation and composition helpers for document relative paths."""

from __future__ import annotations

import re

from agent_bridge.core.domain import ValidationError


_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
_INVALID_SEGMENT_CHARS = set(':*?"<>|')
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def _validate_segment(segment: str, *, basename: bool = False) -> None:
    if not segment or segment in {".", ".."}:
        raise ValidationError("document path contains an invalid path segment")
    if any(ord(char) < 32 or ord(char) == 127 for char in segment):
        raise ValidationError("document path contains a control character")
    if any(char in _INVALID_SEGMENT_CHARS for char in segment):
        raise ValidationError("document path contains an invalid name")
    if segment[-1] in {" ", "."}:
        raise ValidationError("document path contains an invalid name")
    if segment.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
        raise ValidationError("document path contains an invalid name")
    if segment.strip() == "":
        message = "document path contains an invalid name" if basename else "document path contains an invalid directory name"
        raise ValidationError(message)


def _split_raw_path(raw_path: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(raw_path, str):
        raise ValidationError("document path must be a string")
    if not raw_path:
        if allow_empty:
            return []
        raise ValidationError("document path must not be empty")
    normalized = raw_path.replace("\\", "/")
    if normalized.startswith("/") or _DRIVE_PREFIX.match(normalized):
        raise ValidationError("document path must be relative")
    if normalized.endswith("/") and not allow_empty:
        raise ValidationError("document path must have a basename")
    if any(ord(char) < 32 or ord(char) == 127 for char in normalized):
        raise ValidationError("document path contains a control character")

    parts = [part for part in normalized.split("/") if part]
    if not parts and not allow_empty:
        raise ValidationError("document path must have a basename")
    for index, part in enumerate(parts):
        _validate_segment(part, basename=index == len(parts) - 1)
    return parts


def normalize_relative_document_path(raw_path: str) -> str:
    """Return a safe, slash-separated document path relative to a KB folder."""
    return "/".join(_split_raw_path(raw_path))


def split_document_path(path: str) -> tuple[list[str], str]:
    """Split a normalized or raw document path into parent directories and basename."""
    parts = _split_raw_path(path)
    return parts[:-1], parts[-1]


def _normalize_folder_path(folder_path: str) -> str:
    return "/".join(_split_raw_path(folder_path, allow_empty=True))


def join_backend_path(folder_path: str, relative_path: str) -> str:
    """Join a KB folder path and document path without adding a virtual root."""
    folder = _normalize_folder_path(folder_path)
    document = normalize_relative_document_path(relative_path)
    return f"{folder}/{document}" if folder else document
