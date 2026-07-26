"""检索探测领域模型。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ProbeStatus(str, Enum):
    hit = "hit"
    no_hit = "no_hit"
    not_configured = "not_configured"
    unavailable = "unavailable"
    timeout = "timeout"


@dataclass(frozen=True)
class ProbeTarget:
    source_type: str
    resource_key: str
    resource_name: str
    suggested_tool: str

    def to_payload(self) -> dict[str, str]:
        return {
            "source_type": self.source_type,
            "resource_key": self.resource_key,
            "resource_name": self.resource_name,
            "suggested_tool": self.suggested_tool,
        }


@dataclass(frozen=True)
class KeywordProbeResult:
    target: ProbeTarget
    keyword: str
    status: ProbeStatus
    candidate_keys: tuple[str, ...] = ()
    count: int = 0
    capped: bool = False
    duration_ms: int = 0
    error_type: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "keyword": self.keyword,
            "status": self.status.value,
            "count": self.count,
            "capped": self.capped,
            "duration_ms": self.duration_ms,
            "error_type": self.error_type,
        }


@dataclass(frozen=True)
class TargetProbeSummary:
    target: ProbeTarget
    status: ProbeStatus
    unique_hit_count: int
    keyword_hits: tuple[KeywordProbeResult, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            **self.target.to_payload(),
            "status": self.status.value,
            "unique_hit_count": self.unique_hit_count,
            "keyword_hits": [item.to_payload() for item in self.keyword_hits],
        }


@dataclass(frozen=True)
class ProbeResponse:
    probe_id: str
    profile_key: str
    session_id: str
    keywords: tuple[str, ...]
    source_statuses: dict[str, ProbeStatus]
    targets: tuple[TargetProbeSummary, ...]
    duration_ms: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "probe_id": self.probe_id,
            "profile_key": self.profile_key,
            "session_id": self.session_id,
            "keywords": list(self.keywords),
            "source_statuses": {
                source_type: status.value
                for source_type, status in self.source_statuses.items()
            },
            "targets": [target.to_payload() for target in self.targets],
            "duration_ms": self.duration_ms,
        }
