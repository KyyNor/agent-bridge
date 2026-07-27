from __future__ import annotations

import time
from dataclasses import dataclass, field

import pytest

from agent_bridge.app.service import AgentBridgeService
from agent_bridge.core.domain import NotFound, ValidationError
from agent_bridge.knowledge_management.retrieval_probe.models import (
    KeywordProbeResult,
    ProbeStatus,
    ProbeTarget,
)
from agent_bridge.knowledge_management.retrieval_probe.registry import RetrievalProbeRegistry
from agent_bridge.knowledge_management.retrieval_probe.service import RetrievalProbeService
from agent_bridge.knowledge_management.retrieval_probe.tokenizer import (
    extract_probe_keywords,
)


class FakeStore:
    def __init__(self, status: str | None = "active"):
        self.status = status

    def get_project_profile(self, profile_key: str):
        if self.status is None:
            return None
        return {"profile_key": profile_key, "status": self.status}


class FakeGovernance:
    def __init__(self):
        self.logs: list[dict] = []

    def log_tool_call(self, **kwargs):
        self.logs.append(kwargs)
        return {"log_id": "call-1"}


@dataclass
class FakeAdapter:
    source_type: str
    target_keys: tuple[str, ...] = ("target",)
    results: dict[str, tuple[str, ...]] = field(default_factory=dict)
    list_delay: float = 0
    delay: float = 0
    failure: Exception | None = None
    calls: list[tuple[str, str]] = field(default_factory=list)

    def list_targets(self, *, actor: str, profile_key: str):
        if self.list_delay:
            time.sleep(self.list_delay)
        return [
            ProbeTarget(
                source_type=self.source_type,
                resource_key=key,
                resource_name=key,
                suggested_tool=f"{self.source_type}_search",
            )
            for key in self.target_keys
        ]

    def probe(self, *, actor, profile_key, target, keyword, limit):
        self.calls.append((target.resource_key, keyword))
        if self.delay:
            time.sleep(self.delay)
        if self.failure is not None:
            raise self.failure
        keys = self.results.get(keyword, ())
        return KeywordProbeResult(
            target=target,
            keyword=keyword,
            status=ProbeStatus.hit if keys else ProbeStatus.no_hit,
            candidate_keys=keys,
            count=len(keys),
            capped=len(keys) >= limit,
            duration_ms=1,
        )


def registry_with(*adapters: FakeAdapter) -> RetrievalProbeRegistry:
    registry = RetrievalProbeRegistry()
    for adapter in adapters:
        registry.register(adapter)
    return registry


def service_with(
    *adapters: FakeAdapter,
    profile_status: str | None = "active",
) -> tuple[RetrievalProbeService, FakeGovernance]:
    governance = FakeGovernance()
    return (
        RetrievalProbeService(
            store=FakeStore(profile_status),
            registry=registry_with(*adapters),
            governance=governance,
            concurrency=8,
        ),
        governance,
    )


@pytest.mark.asyncio
async def test_probe_queries_every_keyword_and_target_and_deduplicates_candidates() -> None:
    adapter = FakeAdapter(
        source_type="wiki",
        target_keys=("kb-a",),
        results={
            "订单": ("c1", "c2"),
            "同步": ("c2", "c3"),
        },
    )
    service, governance = service_with(adapter)

    response = await service.probe(
        actor="root",
        profile_key="dev",
        prompt="订单同步",
        keyword_limit=8,
        result_limit=3,
        timeout_seconds=1,
        session_id="session-1",
    )

    assert set(adapter.calls) == {("kb-a", "订单"), ("kb-a", "同步")}
    assert response.keywords == ("订单", "同步")
    assert response.targets[0].unique_hit_count == 3
    assert response.source_statuses["wiki"] is ProbeStatus.hit
    assert response.session_id == "session-1"
    assert governance.logs[0]["entrypoint"] == "retrieval_probe"
    assert governance.logs[0]["source_type"] == "hook"
    assert governance.logs[0]["tool_name"] == "full_probe"
    assert governance.logs[0]["request"]["prompt_length"] == 4
    assert "订单同步" not in str(governance.logs[0]["request"])


@pytest.mark.asyncio
async def test_probe_returns_partial_results_and_marks_slow_jobs_timeout() -> None:
    fast = FakeAdapter(
        source_type="artifact",
        results={"订单": ("a1",)},
    )
    slow = FakeAdapter(
        source_type="wiki",
        results={"订单": ("w1",)},
        delay=0.2,
    )
    service, _ = service_with(fast, slow)

    response = await service.probe(
        actor="root",
        profile_key="dev",
        prompt="订单",
        timeout_seconds=0.05,
    )

    assert response.source_statuses["artifact"] is ProbeStatus.hit
    assert response.source_statuses["wiki"] is ProbeStatus.timeout
    wiki = next(target for target in response.targets if target.target.source_type == "wiki")
    assert wiki.keyword_hits[0].status is ProbeStatus.timeout


@pytest.mark.asyncio
async def test_probe_applies_overall_deadline_to_target_discovery() -> None:
    slow = FakeAdapter(source_type="wiki", list_delay=0.2)
    service, _ = service_with(slow)
    extract_probe_keywords("分词预热")
    started = time.monotonic()

    response = await service.probe(
        actor="root",
        profile_key="dev",
        prompt="订单",
        timeout_seconds=0.05,
    )

    assert time.monotonic() - started < 0.15
    assert response.source_statuses["wiki"] is ProbeStatus.timeout
    assert response.targets == ()


@pytest.mark.asyncio
async def test_probe_distinguishes_not_configured_and_unavailable_sources() -> None:
    unconfigured = FakeAdapter(source_type="memory", target_keys=())
    unavailable = FakeAdapter(
        source_type="codegraph",
        failure=RuntimeError("index unavailable"),
    )
    no_hit = FakeAdapter(source_type="wiki")
    service, _ = service_with(unconfigured, unavailable, no_hit)

    response = await service.probe(
        actor="root",
        profile_key="dev",
        prompt="订单",
    )

    assert response.source_statuses == {
        "memory": ProbeStatus.not_configured,
        "codegraph": ProbeStatus.unavailable,
        "wiki": ProbeStatus.no_hit,
    }
    codegraph = next(
        target
        for target in response.targets
        if target.target.source_type == "codegraph"
    )
    assert codegraph.keyword_hits[0].error_type == "RuntimeError"


@pytest.mark.asyncio
async def test_probe_rejects_missing_or_disabled_profile() -> None:
    missing, _ = service_with(profile_status=None)
    disabled, _ = service_with(profile_status="disabled")

    with pytest.raises(NotFound, match="profile not found"):
        await missing.probe(actor="root", profile_key="dev", prompt="订单")
    with pytest.raises(ValidationError, match="profile is disabled"):
        await disabled.probe(actor="root", profile_key="dev", prompt="订单")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"prompt": "  "}, "prompt is required"),
        ({"prompt": "怎么如何"}, "prompt has no searchable keywords"),
        ({"keyword_limit": 0}, "keyword_limit must be positive"),
        ({"result_limit": 0}, "result_limit must be positive"),
        ({"timeout_seconds": 0}, "timeout_seconds must be positive"),
    ],
)
async def test_probe_validates_request_boundaries(kwargs, message) -> None:
    service, _ = service_with(FakeAdapter("wiki"))
    defaults = {"actor": "root", "profile_key": "dev", "prompt": "订单"}
    defaults.update(kwargs)

    with pytest.raises(ValidationError, match=message):
        await service.probe(**defaults)


def test_agent_bridge_service_registers_all_builtin_probe_adapters(wm_paths) -> None:
    service = AgentBridgeService.create(wm_paths, {"root"})

    assert [
        adapter.source_type
        for adapter in service.retrieval_probe.registry.list()
    ] == ["wiki", "codegraph", "memory", "artifact"]
