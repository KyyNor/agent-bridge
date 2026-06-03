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
    overview = "overview"
    search = "search"
    detail = "detail"
    action = "action"


@dataclass(frozen=True)
class PolicyContext:
    actor: str
    profile_key: str
    allow_sources: list[str]
    deny_sources: list[str]
    request_id: str | None = None
    entrypoint: str | None = None


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
