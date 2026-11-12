"""知识库同步策略的解析规则。"""

from __future__ import annotations

import json
from typing import Any, Literal

SyncPolicy = Literal["immediate", "scheduled", "paused"]


def default_retrieval_backend(kb: dict[str, Any], active_slugs: list[str]) -> str | None:
    """未指定默认后端时，与知识库检索一样使用首个启用目标。"""
    return kb.get("default_backend_slug") or next(iter(active_slugs), None)


def target_sync_config(target: dict[str, Any]) -> dict[str, Any]:
    raw_config = target.get("config_json") or "{}"
    try:
        config = json.loads(raw_config) if isinstance(raw_config, str) else raw_config
    except json.JSONDecodeError:
        config = {}
    return config if isinstance(config, dict) else {}


def resolve_sync_policy(
    kb: dict[str, Any], target: dict[str, Any], retrieval_backend: str | None
) -> SyncPolicy:
    config = target_sync_config(target)
    policy = config.get("sync_policy")
    if policy in ("immediate", "scheduled", "paused"):
        return policy
    if "sync_on_upload" in config:
        return "immediate" if config["sync_on_upload"] else "scheduled"
    if kb.get("sync_follows_default"):
        return "scheduled" if target["slug"] == retrieval_backend else "paused"
    return "immediate" if kb.get("sync_on_upload") else "scheduled"
