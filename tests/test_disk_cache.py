from __future__ import annotations

import time

from agent_bridge.core.cache import DEFAULT_CACHE_TTL_SECONDS, DiskCacheStore


def test_disk_cache_store_is_namespaced_and_uses_default_ttl(tmp_path) -> None:
    first = DiskCacheStore(tmp_path, namespace="first")
    second = DiskCacheStore(tmp_path, namespace="second")

    first.set("key", {"value": 1})

    assert first.get("key") == {"value": 1}
    assert second.get("key") is None
    _, expires_at = first._cache.get(first._encoded_key("key"), expire_time=True)
    assert expires_at >= time.time() + DEFAULT_CACHE_TTL_SECONDS - 2

    first.close()
    second.close()


def test_disk_cache_store_supports_custom_expiration_and_clear(tmp_path) -> None:
    cache = DiskCacheStore(tmp_path, namespace="test", default_expire=10)
    cache.set("short", "value", expire=1)
    cache.set("long", "value")

    _, expires_at = cache._cache.get(cache._encoded_key("short"), expire_time=True)
    assert expires_at <= time.time() + 1.5
    assert cache.clear() == 2
    assert cache.get("short") is None
    assert cache.get("long") is None
    cache.close()
