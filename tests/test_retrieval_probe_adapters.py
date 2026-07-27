from __future__ import annotations

from dataclasses import dataclass

import pytest

from agent_bridge.core.domain import RetrievalResult
from agent_bridge.knowledge_management.retrieval_probe.adapters import (
    ArtifactProbeAdapter,
    CodeGraphProbeAdapter,
    MemoryProbeAdapter,
    WikiProbeAdapter,
)
from agent_bridge.knowledge_management.retrieval_probe.models import ProbeStatus
from agent_bridge.knowledge_management.retrieval_probe.registry import RetrievalProbeRegistry


@dataclass
class FakeAdapter:
    source_type: str

    def list_targets(self, *, actor: str, profile_key: str):
        return []

    def probe(self, *, actor, profile_key, target, keyword, limit):
        raise AssertionError("not used")


class FakeGovernance:
    def __init__(self, allowed: list[str]):
        self.allowed = allowed
        self.calls: list[dict] = []

    def filter_resource_keys(self, **kwargs):
        self.calls.append(kwargs)
        return [key for key in kwargs["resource_keys"] if key in self.allowed]


class FakeStore:
    def __init__(self, kbs: list[dict]):
        self.kbs = kbs

    def list_kbs(self):
        return self.kbs


def test_registry_rejects_duplicate_source_type() -> None:
    registry = RetrievalProbeRegistry()
    registry.register(FakeAdapter("wiki"))
    registry.register(FakeAdapter("memory"))

    with pytest.raises(ValueError, match="duplicate retrieval probe adapter"):
        registry.register(FakeAdapter("wiki"))
    assert [adapter.source_type for adapter in registry.list()] == ["wiki", "memory"]


def test_wiki_adapter_lists_only_allowed_kbs_and_maps_chunks() -> None:
    captured = {}

    def search(**kwargs):
        captured.update(kwargs)
        return [
            RetrievalResult("c1", "body", "doc", 0.8, "d1"),
            RetrievalResult("c2", "body", "doc", 0.7, "d1"),
        ]

    governance = FakeGovernance(["allowed"])
    adapter = WikiProbeAdapter(
        store=FakeStore(
            [
                {"slug": "allowed", "name": "Allowed"},
                {"slug": "blocked", "name": "Blocked"},
            ]
        ),
        governance=governance,
        search=search,
    )

    targets = adapter.list_targets(actor="root", profile_key="dev")
    result = adapter.probe(
        actor="root",
        profile_key="dev",
        target=targets[0],
        keyword="订单",
        limit=2,
    )

    assert [target.resource_key for target in targets] == ["allowed"]
    assert targets[0].suggested_tool == "wiki_ask"
    assert captured == {
        "actor": "root",
        "kb_slug": "allowed",
        "question": "订单",
        "profile_key": "dev",
        "top_k": 2,
    }
    assert result.status is ProbeStatus.hit
    assert result.candidate_keys == ("c1", "c2")
    assert result.capped is True


class FakeCodeGraph:
    def __init__(self):
        self.search_calls: list[tuple[str, str, str, int]] = []

    def list_repositories(self, actor: str):
        return [
            {"repo_key": "active", "name": "Active Repo", "status": "active"},
            {"repo_key": "blocked", "name": "Blocked Repo", "status": "active"},
            {"repo_key": "disabled", "name": "Disabled Repo", "status": "disabled"},
        ]

    def search_code(self, actor: str, repo_key: str, query: str, limit: int):
        self.search_calls.append((actor, repo_key, query, limit))
        return [
            {
                "path": "src/orders.py",
                "symbol": "sync_order",
                "kind": "function",
                "line_start": 42,
            }
        ]


def test_codegraph_adapter_filters_active_repositories_and_uses_lightweight_query() -> None:
    codegraph = FakeCodeGraph()
    adapter = CodeGraphProbeAdapter(
        codegraph=codegraph,
        governance=FakeGovernance(["active"]),
    )

    targets = adapter.list_targets(actor="root", profile_key="dev")
    result = adapter.probe(
        actor="root",
        profile_key="dev",
        target=targets[0],
        keyword="sync_order",
        limit=3,
    )

    assert [target.resource_key for target in targets] == ["active"]
    assert targets[0].suggested_tool == "codegraph_explore"
    assert codegraph.search_calls == [("root", "active", "sync_order", 3)]
    assert result.candidate_keys == ("function:src/orders.py:sync_order:42",)
    assert result.capped is False


class FakeMemory:
    def __init__(self, *, configured: bool = True, status: str = "ok"):
        self.configured = configured
        self.status = status
        self.search_calls: list[dict] = []

    def resolve_profile_block(self, actor: str, profile_key: str):
        if not self.configured:
            return {"status": "not_configured", "block": None}
        return {
            "status": "ok",
            "block": {"block_key": "dev-memory", "name": "Dev Memory"},
        }

    def search(self, **kwargs):
        self.search_calls.append(kwargs)
        return {
            "status": self.status,
            "block_key": "dev-memory",
            "items": [{"id": "obs-1", "summary": "订单同步"}] if self.status == "ok" else [],
        }


def test_memory_adapter_uses_bound_block_and_maps_worker_failure() -> None:
    memory = FakeMemory(status="worker_error")
    adapter = MemoryProbeAdapter(memory=memory)

    targets = adapter.list_targets(actor="root", profile_key="dev")
    result = adapter.probe(
        actor="root",
        profile_key="dev",
        target=targets[0],
        keyword="订单",
        limit=3,
    )

    assert targets[0].resource_key == "dev-memory"
    assert targets[0].suggested_tool == "memory_search"
    assert result.status is ProbeStatus.unavailable
    assert result.error_type == "worker_error"
    assert memory.search_calls == [
        {
            "actor": "root",
            "profile_key": "dev",
            "block_key": "dev-memory",
            "query": "订单",
            "limit": 3,
        }
    ]


def test_memory_adapter_returns_no_targets_when_profile_has_no_binding() -> None:
    adapter = MemoryProbeAdapter(memory=FakeMemory(configured=False))

    assert adapter.list_targets(actor="root", profile_key="dev") == []


class FakeWorkflows:
    def __init__(self):
        self.calls: list[dict] = []

    def search_artifacts(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "items": [
                {"artifact_id": "artifact-1"},
                {"artifact_id": "artifact-2"},
            ]
        }


def test_artifact_adapter_searches_current_profile_artifacts() -> None:
    workflows = FakeWorkflows()
    adapter = ArtifactProbeAdapter(workflows=workflows)

    targets = adapter.list_targets(actor="root", profile_key="dev")
    result = adapter.probe(
        actor="root",
        profile_key="dev",
        target=targets[0],
        keyword="订单",
        limit=2,
    )

    assert targets[0].resource_key == "dev"
    assert targets[0].suggested_tool == "artifacts_search"
    assert result.candidate_keys == ("artifact-1", "artifact-2")
    assert result.capped is True
    assert workflows.calls == [
        {
            "actor": "root",
            "profile_key": "dev",
            "query": "订单",
            "tags": [],
            "path": None,
            "workflow_key": None,
            "task_key": None,
            "task_version": None,
            "run_id": None,
            "include_history": False,
            "trusted_profile_context": True,
            "full": False,
            "format": None,
            "limit": 2,
            "paginated": False,
        }
    ]
