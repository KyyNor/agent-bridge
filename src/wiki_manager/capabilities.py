from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class McpServiceStatus(str, Enum):
    enabled = "enabled"
    disabled = "disabled"
    error = "error"


class ToolType(str, Enum):
    overview = "overview"
    search = "search"
    detail = "detail"
    action = "action"


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
