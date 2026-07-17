from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class CodingAgentCapabilities:
    supports_mcp: bool = False
    supports_native_json_schema: bool = False
    supports_skills: bool = False
    supports_subagents: bool = False
    supports_cost: bool = False
    supports_turn_count: bool = False
    supports_abort: bool = False
    supports_partial_messages: bool = False


@dataclass(frozen=True)
class CodingAgentRequest:
    prompt: str
    cwd: Path
    mcp_servers: Path | str | dict[str, Any]
    setting_sources: list[str]
    output_schema: dict[str, Any] | None = None
    system_prompt_append: str = ""
    include_partial_messages: bool = False
    skills: list[str] | None = None
    stderr: Callable[[str], None] | None = None
    model: str | None = None
    max_turns: int | None = None
    max_budget_usd: float | None = None
    # Compatibility hook for callers that still inspect the native SDK/CLI
    # stream. AgentService itself must not depend on the native message shape.
    on_native_message: Callable[[Any], None] | None = None

    def with_model(self, model: str) -> "CodingAgentRequest":
        return CodingAgentRequest(
            prompt=self.prompt,
            cwd=self.cwd,
            mcp_servers=self.mcp_servers,
            setting_sources=self.setting_sources,
            output_schema=self.output_schema,
            system_prompt_append=self.system_prompt_append,
            include_partial_messages=self.include_partial_messages,
            skills=self.skills,
            stderr=self.stderr,
            model=model,
            max_turns=self.max_turns,
            max_budget_usd=self.max_budget_usd,
            on_native_message=self.on_native_message,
        )


@dataclass(frozen=True)
class CodingAgentFinal:
    is_error: bool = False
    result: str | None = None
    structured_output: Any | None = None
    subtype: str | None = None
    session_id: str | None = None
    cost_usd: float | None = None
    num_turns: int | None = None
    model: str | None = None


@dataclass(frozen=True)
class CodingAgentUpdate:
    raw: dict[str, Any] | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    final: CodingAgentFinal | None = None


class CodingAgentRun(Protocol):
    async def updates(self) -> AsyncIterator[CodingAgentUpdate]: ...

    async def abort(self) -> None: ...


class CodingAgent(Protocol):
    backend_key: str
    display_name: str
    source: str
    capabilities: CodingAgentCapabilities

    def start(self, request: CodingAgentRequest) -> CodingAgentRun: ...
