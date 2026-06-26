from __future__ import annotations

from typing import Any


ACTIVE_MEMORY_STATUSES = {"active", "disabled"}
NOOP_HOOK_STDOUT = '{"continue":true,"suppressOutput":true}'


def normalized_search_item(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(raw.get("id") or raw.get("observation_id") or ""),
        "summary": str(raw.get("summary") or raw.get("title") or ""),
        "content_preview": str(raw.get("content_preview") or raw.get("preview") or raw.get("content") or "")[:1000],
        "score": raw.get("score"),
        "timestamp": raw.get("timestamp") or raw.get("created_at"),
        "metadata": raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {},
    }


def normalized_timeline_item(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(raw.get("id") or raw.get("observation_id") or ""),
        "event_type": str(raw.get("event_type") or raw.get("type") or ""),
        "summary": str(raw.get("summary") or raw.get("title") or ""),
        "timestamp": raw.get("timestamp") or raw.get("created_at"),
        "metadata": raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {},
    }
