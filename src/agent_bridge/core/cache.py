"""基于 DiskCache 的公共磁盘缓存封装。"""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from diskcache import Cache


DEFAULT_CACHE_TTL_SECONDS = 8 * 60 * 60


class DiskCacheStore:
    """带命名空间和默认 TTL 的 DiskCache 封装。

    调用方只负责提供可 JSON 序列化的 key；具体 key 编码、命名空间隔离
    和底层 Cache 生命周期由本类统一处理。
    """

    def __init__(
        self,
        cache_dir: Path | str,
        *,
        namespace: str,
        default_expire: float = DEFAULT_CACHE_TTL_SECONDS,
    ) -> None:
        if not namespace.strip():
            raise ValueError("cache namespace must not be empty")
        if default_expire <= 0:
            raise ValueError("cache default expiration must be positive")
        self.cache_dir = Path(cache_dir)
        self.namespace = namespace.strip()
        self.default_expire = default_expire
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache = Cache(str(self.cache_dir))

    def get(self, key: Any, default: Any = None, **kwargs: Any) -> Any:
        return self._cache.get(self._encoded_key(key), default, **kwargs)

    def set(self, key: Any, value: Any, *, expire: float | None = None, **kwargs: Any) -> bool:
        ttl = self.default_expire if expire is None else expire
        if ttl <= 0:
            raise ValueError("cache expiration must be positive")
        return self._cache.set(self._encoded_key(key), value, expire=ttl, **kwargs)

    def delete(self, key: Any) -> bool:
        return self._cache.delete(self._encoded_key(key))

    def clear(self) -> int:
        """清理当前命名空间，不影响同一目录下的其他缓存。"""
        prefix = f"{self.namespace}:"
        deleted = 0
        for key in list(self._cache.iterkeys()):
            if isinstance(key, str) and key.startswith(prefix) and self._cache.delete(key):
                deleted += 1
        return deleted

    @contextmanager
    def transact(self, **kwargs: Any) -> Iterator["DiskCacheStore"]:
        with self._cache.transact(**kwargs):
            yield self

    def close(self) -> None:
        self._cache.close()

    def _encoded_key(self, key: Any) -> str:
        if isinstance(key, str):
            raw = key
        else:
            raw = json.dumps(key, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return f"{self.namespace}:{digest}"
