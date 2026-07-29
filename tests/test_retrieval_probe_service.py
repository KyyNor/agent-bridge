from __future__ import annotations

import json
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
from agent_bridge.knowledge_management.memory.models import NOOP_HOOK_STDOUT
from agent_bridge.knowledge_management.retrieval_probe.registry import RetrievalProbeRegistry
from agent_bridge.knowledge_management.retrieval_probe.service import RetrievalProbeService
from agent_bridge.knowledge_management.retrieval_probe.extractor import (
    KeywordExtraction,
    KeywordExtractionStatus,
)
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


class FakeExtractor:
    def extract(self, prompt, *, profile_key="", session_id="", max_keywords, timeout_seconds):
        if max_keywords == 0:
            return KeywordExtraction(
                status=KeywordExtractionStatus.success,
                keywords=(),
                model="fake",
            )
        keywords = tuple(extract_probe_keywords(prompt, max_keywords))
        if len(keywords) < 2:
            keywords = ("订单", "同步")
        return KeywordExtraction(
            status=KeywordExtractionStatus.success,
            keywords=keywords,
            model="fake",
        )


class FailedExtractor:
    def extract(self, prompt, *, profile_key="", session_id="", max_keywords, timeout_seconds):
        return KeywordExtraction(status=KeywordExtractionStatus.invalid_output, error_type="invalid_output")


class RecordingExtractor(FakeExtractor):
    def __init__(self):
        self.calls = []

    def extract(self, prompt, *, profile_key="", session_id="", max_keywords, timeout_seconds):
        self.calls.append({
            "prompt": prompt,
            "profile_key": profile_key,
            "session_id": session_id,
            "max_keywords": max_keywords,
            "timeout_seconds": timeout_seconds,
        })
        return KeywordExtraction(
            status=KeywordExtractionStatus.success,
            keywords=("新术语",),
            model="fake",
        )


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
            keyword_extractor=FakeExtractor(),
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
    assert governance.logs[0]["entrypoint"] == "retrieval_probe_api"
    assert governance.logs[0]["source_type"] == "builtin"
    assert governance.logs[0]["tool_name"] == "retrieval-probe"
    assert governance.logs[0]["request"]["prompt_length"] == 4
    assert "订单同步" not in str(governance.logs[0]["request"])


@pytest.mark.asyncio
async def test_probe_does_not_search_when_keyword_extraction_fails() -> None:
    adapter = FakeAdapter(source_type="artifact")
    governance = FakeGovernance()
    service = RetrievalProbeService(
        store=FakeStore(),
        registry=registry_with(adapter),
        governance=governance,
        keyword_extractor=FailedExtractor(),
    )
    response = await service.probe(actor="root", profile_key="dev", prompt="任意问题")
    assert response.keywords == ()
    assert response.targets == ()
    assert adapter.calls == []
    assert response.keyword_extraction.status is KeywordExtractionStatus.invalid_output


@pytest.mark.asyncio
async def test_full_probe_hook_returns_standard_context_and_records_raw_payload() -> None:
    service, governance = service_with(
        FakeAdapter(source_type="wiki", results={"订单": ("chunk-1",)})
    )
    raw_payload = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": "session-1",
        "cwd": "/repo",
        "prompt": "订单同步失败",
    }

    result = await service.handle_claude_code_hook(
        actor="root",
        profile_key="dev",
        event_name="UserPromptSubmit",
        matcher=None,
        payload=raw_payload,
        timeout_seconds=12,
    )

    stdout = json.loads(result["stdout"])
    assert stdout["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert "delivery_id:" in stdout["hookSpecificOutput"]["additionalContext"]
    assert result["stderr"] == ""
    assert result["exit_code"] == 0
    assert result["status"] == "ok"
    assert governance.logs == [
        {
            "actor": "root",
            "profile_key": "dev",
            "entrypoint": "retrieval_probe_hook_claude_code",
            "source_type": "hook",
            "source_key": "claude_code",
            "tool_name": "full-probe",
            "request": {
                "action": "full-probe",
                "event_name": "UserPromptSubmit",
                "matcher": None,
                "payload": raw_payload,
                "timeout_seconds": 12,
                "source": "claude-code",
            },
            "response": result,
            "status": "success",
            "error_message": None,
            "duration_ms": governance.logs[0]["duration_ms"],
            "error_type": None,
        }
    ]


@pytest.mark.asyncio
async def test_full_probe_hook_returns_noop_and_success_audit_when_nothing_hits() -> None:
    service, governance = service_with(FakeAdapter(source_type="wiki"))

    result = await service.handle_claude_code_hook(
        actor="root",
        profile_key="dev",
        event_name="UserPromptSubmit",
        matcher=None,
        payload={"prompt": "订单", "session_id": "session-1"},
        timeout_seconds=12,
    )

    assert result == {
        "stdout": NOOP_HOOK_STDOUT,
        "stderr": "",
        "exit_code": 0,
        "status": "ok",
    }
    assert governance.logs[0]["status"] == "success"


@pytest.mark.asyncio
async def test_full_probe_hook_audits_exception_before_reraising() -> None:
    service, governance = service_with(FakeAdapter("wiki"), profile_status=None)
    raw_payload = {"prompt": "订单", "session_id": "session-1"}

    with pytest.raises(NotFound, match="profile not found"):
        await service.handle_claude_code_hook(
            actor="root",
            profile_key="dev",
            event_name="UserPromptSubmit",
            matcher=None,
            payload=raw_payload,
            timeout_seconds=12,
        )

    assert governance.logs[0]["tool_name"] == "full-probe"
    assert governance.logs[0]["request"]["payload"] == raw_payload
    assert governance.logs[0]["response"] == {
        "exception_type": "NotFound",
        "message": "profile not found",
    }
    assert governance.logs[0]["status"] == "error"


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
        ({"result_limit": 0}, "result_limit must be positive"),
        ({"timeout_seconds": 0}, "timeout_seconds must be between 0 and 20"),
    ],
)
async def test_probe_validates_request_boundaries(kwargs, message) -> None:
    service, _ = service_with(FakeAdapter("wiki"))
    defaults = {"actor": "root", "profile_key": "dev", "prompt": "订单"}
    defaults.update(kwargs)

    with pytest.raises(ValidationError, match=message):
        await service.probe(**defaults)


@pytest.mark.asyncio
async def test_probe_allows_zero_keyword_limit_without_searching() -> None:
    adapter = FakeAdapter("wiki")
    service, _ = service_with(adapter)
    response = await service.probe(
        actor="root",
        profile_key="dev",
        prompt="订单",
        keyword_limit=0,
    )
    assert response.keywords == ()
    assert response.targets == ()
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_probe_forwards_profile_and_session_to_extractor() -> None:
    extractor = RecordingExtractor()
    adapter = FakeAdapter("artifact")
    service = RetrievalProbeService(
        store=FakeStore(),
        registry=registry_with(adapter),
        governance=FakeGovernance(),
        keyword_extractor=extractor,
    )
    await service.probe(
        actor="root",
        profile_key="dev",
        prompt="问题",
        session_id="session-1",
        timeout_seconds=1,
    )
    assert extractor.calls[0]["profile_key"] == "dev"
    assert extractor.calls[0]["session_id"] == "session-1"


def test_agent_bridge_service_registers_artifact_probe_adapter_only(wm_paths) -> None:
    service = AgentBridgeService.create(wm_paths, {"root"})

    assert [
        adapter.source_type
        for adapter in service.retrieval_probe.registry.list()
    ] == ["artifact"]
