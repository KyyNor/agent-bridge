"""内置知识来源的轻量探测 adapter。"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from agent_bridge.capability_hub.models import ProfileResourceType

from .models import KeywordProbeResult, ProbeStatus, ProbeTarget


@runtime_checkable
class RetrievalProbeAdapter(Protocol):
    source_type: str

    def list_targets(self, *, actor: str, profile_key: str) -> list[ProbeTarget]: ...

    def probe(
        self,
        *,
        actor: str,
        profile_key: str,
        target: ProbeTarget,
        keyword: str,
        limit: int,
    ) -> KeywordProbeResult: ...


class WikiProbeAdapter:
    source_type = "wiki"

    def __init__(self, *, store: Any, governance: Any, search: Callable[..., Any]) -> None:
        self.store = store
        self.governance = governance
        self.search = search

    def list_targets(self, *, actor: str, profile_key: str) -> list[ProbeTarget]:
        kbs = self.store.list_kbs()
        allowed = set(
            self.governance.filter_resource_keys(
                actor=actor,
                profile_key=profile_key,
                resource_type=ProfileResourceType.wiki_kb.value,
                resource_keys=[str(kb["slug"]) for kb in kbs],
            )
        )
        return [
            ProbeTarget(
                source_type=self.source_type,
                resource_key=str(kb["slug"]),
                resource_name=str(kb.get("name") or kb["slug"]),
                suggested_tool="wiki_ask",
            )
            for kb in kbs
            if kb["slug"] in allowed
        ]

    def probe(
        self,
        *,
        actor: str,
        profile_key: str,
        target: ProbeTarget,
        keyword: str,
        limit: int,
    ) -> KeywordProbeResult:
        started = time.monotonic()
        chunks = self.search(
            actor=actor,
            kb_slug=target.resource_key,
            question=keyword,
            profile_key=profile_key,
            top_k=limit,
        )
        keys = _unique_keys(
            str(getattr(chunk, "chunk_id", "") or "")
            or _fallback_key(self.source_type, getattr(chunk, "__dict__", chunk))
            for chunk in chunks
        )
        return _result(target, keyword, keys, limit, started)


class CodeGraphProbeAdapter:
    source_type = "codegraph"

    def __init__(self, *, codegraph: Any, governance: Any) -> None:
        self.codegraph = codegraph
        self.governance = governance

    def list_targets(self, *, actor: str, profile_key: str) -> list[ProbeTarget]:
        repos = [
            repo
            for repo in self.codegraph.list_repositories(actor)
            if repo.get("status") == "active"
        ]
        allowed = set(
            self.governance.filter_resource_keys(
                actor=actor,
                profile_key=profile_key,
                resource_type=ProfileResourceType.code_repo.value,
                resource_keys=[str(repo["repo_key"]) for repo in repos],
            )
        )
        return [
            ProbeTarget(
                source_type=self.source_type,
                resource_key=str(repo["repo_key"]),
                resource_name=str(repo.get("name") or repo["repo_key"]),
                suggested_tool="codegraph_explore",
            )
            for repo in repos
            if repo["repo_key"] in allowed
        ]

    def probe(
        self,
        *,
        actor: str,
        profile_key: str,
        target: ProbeTarget,
        keyword: str,
        limit: int,
    ) -> KeywordProbeResult:
        del profile_key
        started = time.monotonic()
        nodes = self.codegraph.search_code(
            actor,
            target.resource_key,
            query=keyword,
            limit=limit,
        )
        keys = _unique_keys(_codegraph_key(node) for node in nodes)
        return _result(target, keyword, keys, limit, started)


class MemoryProbeAdapter:
    source_type = "memory"

    def __init__(self, *, memory: Any) -> None:
        self.memory = memory

    def list_targets(self, *, actor: str, profile_key: str) -> list[ProbeTarget]:
        resolved = self.memory.resolve_profile_block(actor, profile_key)
        if resolved.get("status") != "ok" or not resolved.get("block"):
            return []
        block = resolved["block"]
        return [
            ProbeTarget(
                source_type=self.source_type,
                resource_key=str(block["block_key"]),
                resource_name=str(block.get("name") or block["block_key"]),
                suggested_tool="memory_search",
            )
        ]

    def probe(
        self,
        *,
        actor: str,
        profile_key: str,
        target: ProbeTarget,
        keyword: str,
        limit: int,
    ) -> KeywordProbeResult:
        started = time.monotonic()
        response = self.memory.search(
            actor=actor,
            profile_key=profile_key,
            block_key=target.resource_key,
            query=keyword,
            limit=limit,
        )
        status = str(response.get("status") or "")
        if status != "ok":
            return KeywordProbeResult(
                target=target,
                keyword=keyword,
                status=ProbeStatus.unavailable,
                duration_ms=_elapsed_ms(started),
                error_type=status or "memory_search_error",
            )
        items = response.get("items") if isinstance(response.get("items"), list) else []
        keys = _unique_keys(
            str(item.get("id") or "")
            or _fallback_key(self.source_type, item)
            for item in items
            if isinstance(item, dict)
        )
        return _result(target, keyword, keys, limit, started)


class ArtifactProbeAdapter:
    source_type = "artifact"

    def __init__(self, *, workflows: Any) -> None:
        self.workflows = workflows

    def list_targets(self, *, actor: str, profile_key: str) -> list[ProbeTarget]:
        del actor
        return [
            ProbeTarget(
                source_type=self.source_type,
                resource_key=profile_key,
                resource_name="工作流产出物",
                suggested_tool="artifacts_search",
            )
        ]

    def probe(
        self,
        *,
        actor: str,
        profile_key: str,
        target: ProbeTarget,
        keyword: str,
        limit: int,
    ) -> KeywordProbeResult:
        started = time.monotonic()
        response = self.workflows.search_artifacts(
            actor=actor,
            profile_key=profile_key,
            query=keyword,
            tags=[],
            path=None,
            workflow_key=None,
            task_key=None,
            task_version=None,
            run_id=None,
            include_history=False,
            trusted_profile_context=True,
            full=False,
            format=None,
            limit=limit,
            paginated=False,
        )
        items = response.get("items") if isinstance(response.get("items"), list) else []
        keys = _unique_keys(
            str(item.get("artifact_id") or "")
            or _fallback_key(self.source_type, item)
            for item in items
            if isinstance(item, dict)
        )
        return _result(target, keyword, keys, limit, started)


def _result(
    target: ProbeTarget,
    keyword: str,
    candidate_keys: tuple[str, ...],
    limit: int,
    started: float,
) -> KeywordProbeResult:
    count = len(candidate_keys)
    return KeywordProbeResult(
        target=target,
        keyword=keyword,
        status=ProbeStatus.hit if count else ProbeStatus.no_hit,
        candidate_keys=candidate_keys,
        count=count,
        capped=count >= limit,
        duration_ms=_elapsed_ms(started),
    )


def _unique_keys(keys) -> tuple[str, ...]:
    return tuple(dict.fromkeys(key for key in keys if key))


def _codegraph_key(node: dict[str, Any]) -> str:
    return ":".join(
        [
            str(node.get("kind") or ""),
            str(node.get("path") or ""),
            str(node.get("symbol") or ""),
            str(node.get("line_start") or ""),
        ]
    )


def _fallback_key(source_type: str, value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return f"{source_type}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
