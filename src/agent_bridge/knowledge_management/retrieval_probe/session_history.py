"""full-probe 会话历史的磁盘缓存。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from diskcache import Cache


SESSION_HISTORY_RETAINED_ROUNDS = 12
SESSION_HISTORY_PROMPT_ROUNDS = 3
SESSION_HISTORY_TTL_SECONDS = 30 * 24 * 60 * 60


@dataclass(frozen=True)
class ProbeHistoryEntry:
    prompt: str
    keywords: tuple[str, ...]
    created_at: str


@runtime_checkable
class ProbeSessionHistoryStoreProtocol(Protocol):
    def recent(self, profile_key: str, session_id: str, limit: int) -> tuple[ProbeHistoryEntry, ...]: ...

    def append(self, profile_key: str, session_id: str, entry: ProbeHistoryEntry) -> None: ...


class ProbeSessionHistoryStore:
    def __init__(self, cache_dir) -> None:
        self.cache_dir = cache_dir
        self._cache = Cache(str(cache_dir))

    def recent(self, profile_key: str, session_id: str, limit: int) -> tuple[ProbeHistoryEntry, ...]:
        if not session_id or limit < 1:
            return ()
        key = self._key(profile_key, session_id)
        payload = self._cache.get(key)
        if not isinstance(payload, list):
            if payload is not None:
                self._cache.delete(key)
            return ()
        entries: list[ProbeHistoryEntry] = []
        for item in payload:
            if not isinstance(item, dict) or not isinstance(item.get("prompt"), str):
                self._cache.delete(self._key(profile_key, session_id))
                return ()
            keywords = item.get("keywords")
            if not isinstance(keywords, list) or not all(isinstance(v, str) for v in keywords):
                self._cache.delete(self._key(profile_key, session_id))
                return ()
            entries.append(ProbeHistoryEntry(item["prompt"], tuple(keywords), str(item.get("created_at") or "")))
        return tuple(entries[-limit:])

    def append(self, profile_key: str, session_id: str, entry: ProbeHistoryEntry) -> None:
        if not session_id:
            return
        key = self._key(profile_key, session_id)
        with self._cache.transact(retry=True):
            payload = self._cache.get(key)
            history = payload if isinstance(payload, list) and all(
                isinstance(item, dict)
                and isinstance(item.get("prompt"), str)
                and isinstance(item.get("keywords"), list)
                and all(isinstance(value, str) for value in item["keywords"])
                for item in payload
            ) else []
            history.append({"prompt": entry.prompt, "keywords": list(entry.keywords), "created_at": entry.created_at})
            self._cache.set(key, history[-SESSION_HISTORY_RETAINED_ROUNDS:], expire=SESSION_HISTORY_TTL_SECONDS)

    @staticmethod
    def _key(profile_key: str, session_id: str) -> str:
        raw = f"{profile_key}\0{session_id}".encode("utf-8")
        return "probe-session:" + hashlib.sha256(raw).hexdigest()

    def close(self) -> None:
        self._cache.close()
