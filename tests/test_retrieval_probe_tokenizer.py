from __future__ import annotations

import pytest

from agent_bridge.knowledge_management.retrieval_probe.models import (
    KeywordProbeResult,
    ProbeResponse,
    ProbeStatus,
    ProbeTarget,
    TargetProbeSummary,
)
from agent_bridge.knowledge_management.retrieval_probe.tokenizer import extract_probe_keywords


def test_extract_probe_keywords_splits_chinese_and_keeps_identifiers() -> None:
    assert extract_probe_keywords(
        "之前订单同步失败，检查 OrderSyncService 和 src/order_sync.py 的 ERR-1042"
    ) == [
        "订单",
        "同步",
        "失败",
        "OrderSyncService",
        "src/order_sync.py",
        "ERR-1042",
    ]


def test_extract_probe_keywords_deduplicates_and_applies_limit() -> None:
    assert extract_probe_keywords("订单 订单 同步 失败 补偿", limit=3) == [
        "订单",
        "同步",
        "失败",
    ]


def test_extract_probe_keywords_rejects_invalid_limit() -> None:
    with pytest.raises(ValueError, match="limit must be positive"):
        extract_probe_keywords("订单", limit=0)


def test_probe_response_payload_omits_internal_candidate_keys() -> None:
    target = ProbeTarget(
        source_type="wiki",
        resource_key="data-platform",
        resource_name="数据平台",
        suggested_tool="wiki_ask",
    )
    keyword_result = KeywordProbeResult(
        target=target,
        keyword="订单",
        status=ProbeStatus.hit,
        candidate_keys=("chunk-1", "chunk-2"),
        count=2,
        capped=False,
        duration_ms=12,
    )
    summary = TargetProbeSummary(
        target=target,
        status=ProbeStatus.hit,
        unique_hit_count=2,
        keyword_hits=(keyword_result,),
    )
    response = ProbeResponse(
        probe_id="probe_test",
        profile_key="dev",
        session_id="session-1",
        keywords=("订单",),
        source_statuses={"wiki": ProbeStatus.hit},
        targets=(summary,),
        duration_ms=15,
    )

    payload = response.to_payload()

    assert payload["targets"][0]["keyword_hits"][0] == {
        "keyword": "订单",
        "status": "hit",
        "count": 2,
        "capped": False,
        "duration_ms": 12,
        "error_type": None,
    }
    assert "candidate_keys" not in str(payload)
