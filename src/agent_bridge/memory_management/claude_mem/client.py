from __future__ import annotations

import logging
from typing import Any

import httpx

from agent_bridge.memory_management.models import normalized_search_item, normalized_timeline_item


logger = logging.getLogger(__name__)


class ClaudeMemClient:
    """与 claude-mem worker HTTP 服务通信的薄客户端（search / timeline / observation）。"""

    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def search(self, query: str, limit: int) -> dict[str, Any]:
        try:
            response = httpx.get(
                f"{self.base_url}/api/search",
                params={"query": query, "q": query, "limit": limit},
                timeout=self.timeout,
            )
            response.raise_for_status()
        except httpx.TimeoutException:
            logger.error("claude-mem 检索超时 base_url=%s query=%s timeout=%ss", self.base_url, query, self.timeout)
            raise
        except Exception:
            logger.error("claude-mem 检索请求失败 base_url=%s query=%s", self.base_url, query, exc_info=True)
            raise
        payload = response.json()
        if isinstance(payload, dict) and payload.get("isError"):
            raise RuntimeError(_content_text(payload) or "claude-mem search failed")
        raw_items = payload.get("items") if isinstance(payload, dict) else payload
        items = raw_items if isinstance(raw_items, list) else []
        return {"items": [normalized_search_item(item) for item in items if isinstance(item, dict)]}

    def timeline(self, limit: int, cursor: str | None) -> dict[str, Any]:
        offset = _parse_offset(cursor)
        try:
            response = httpx.get(
                f"{self.base_url}/api/observations",
                params={"limit": limit, "offset": offset},
                timeout=self.timeout,
            )
            response.raise_for_status()
        except httpx.TimeoutException:
            logger.error("claude-mem 时间线超时 base_url=%s timeout=%ss", self.base_url, self.timeout)
            raise
        except Exception:
            logger.error("claude-mem 时间线请求失败 base_url=%s", self.base_url, exc_info=True)
            raise
        payload = response.json()
        if isinstance(payload, dict) and payload.get("isError"):
            raise RuntimeError(_content_text(payload) or "claude-mem timeline failed")
        raw_items = payload.get("items") if isinstance(payload, dict) else []
        items = raw_items if isinstance(raw_items, list) else []
        has_more = bool(payload.get("hasMore") or payload.get("has_more")) if isinstance(payload, dict) else False
        next_cursor = str(offset + limit) if has_more else None
        return {
            "items": [normalized_timeline_item(item) for item in items if isinstance(item, dict)],
            "next_cursor": next_cursor,
        }

    def get_observation(self, observation_id: str) -> dict[str, Any]:
        try:
            response = httpx.get(f"{self.base_url}/api/observation/{observation_id}", timeout=self.timeout)
            response.raise_for_status()
        except httpx.TimeoutException:
            logger.error("claude-mem 读取观察超时 base_url=%s observation_id=%s", self.base_url, observation_id)
            raise
        except Exception:
            logger.error("claude-mem 读取观察失败 base_url=%s observation_id=%s", self.base_url, observation_id, exc_info=True)
            raise
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


def _parse_offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        return max(0, int(cursor))
    except ValueError:
        return 0


def _content_text(payload: dict[str, Any]) -> str:
    content = payload.get("content")
    if not isinstance(content, list):
        return ""
    parts = [str(item.get("text") or "") for item in content if isinstance(item, dict)]
    return "\n".join(part for part in parts if part)
