from __future__ import annotations

from typing import Any

import httpx

from agent_bridge.memory_management.models import normalized_search_item, normalized_timeline_item


class ClaudeMemClient:
    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def search(self, query: str, limit: int) -> dict[str, Any]:
        response = httpx.get(
            f"{self.base_url}/api/search",
            params={"q": query, "limit": limit},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        raw_items = payload.get("items") if isinstance(payload, dict) else payload
        items = raw_items if isinstance(raw_items, list) else []
        return {"items": [normalized_search_item(item) for item in items if isinstance(item, dict)]}

    def timeline(self, limit: int, cursor: str | None) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        response = httpx.get(f"{self.base_url}/api/timeline", params=params, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        items = payload.get("items") if isinstance(payload, dict) else []
        next_cursor = payload.get("next_cursor") if isinstance(payload, dict) else None
        return {
            "items": [normalized_timeline_item(item) for item in items if isinstance(item, dict)],
            "next_cursor": next_cursor,
        }

    def get_observation(self, observation_id: str) -> dict[str, Any]:
        response = httpx.get(f"{self.base_url}/api/observation/{observation_id}", timeout=self.timeout)
        response.raise_for_status()
        raw = response.json()
        item = raw if isinstance(raw, dict) else {"content": raw}
        return {
            "item": {
                "id": str(item.get("id") or item.get("observation_id") or observation_id),
                "content": item.get("content") or item.get("text") or "",
                "metadata": item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
                "raw": item,
            }
        }
