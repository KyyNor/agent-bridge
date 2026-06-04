from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class McpServiceStatus(str, Enum):
    enabled = "enabled"
    disabled = "disabled"
    error = "error"


class SourceType(str, Enum):
    mcp_service = "mcp_service"


class ProfileRuleEffect(str, Enum):
    allow = "allow"
    deny = "deny"


class CallLogStatus(str, Enum):
    success = "success"
    error = "error"
    blocked = "blocked"


class ToolType(str, Enum):
    unconfigured = "unconfigured"
    overview = "overview"
    search = "search"
    detail = "detail"
    action = "action"


@dataclass(frozen=True)
class SourceRef:
    source_type: str
    source_key: str


@dataclass(frozen=True)
class PolicyContext:
    actor: str
    profile_key: str | None = None
    allow_sources: set[SourceRef] | None = None
    deny_sources: set[SourceRef] | None = None
    request_id: str | None = None
    entrypoint: str = "metamcp_search"


@dataclass(frozen=True)
class SearchRequest:
    path: str | None = None
    query: str | None = None
    limit: int = 20


@dataclass(frozen=True)
class ExecuteRequest:
    service: str
    tool: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ExecuteResult:
    service: str
    tool: str
    success: bool
    result: Any
    error: str | None = None
