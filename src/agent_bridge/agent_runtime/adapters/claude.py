from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, query as claude_query
from claude_agent_sdk.types import ResultMessage

from agent_bridge.agent_runtime.claude import claude_settings_env
from agent_bridge.agent_runtime.events import (
    Attribution,
    is_noisy_partial_message,
    message_events,
    message_log_record,
)
from agent_bridge.agent_runtime.types import (
    CodingAgentCapabilities,
    CodingAgentFinal,
    CodingAgentRequest,
    CodingAgentRun,
    CodingAgentUpdate,
)


@dataclass
class _ClaudeRun:
    request: CodingAgentRequest
    effort: str | None = None

    async def updates(self) -> AsyncIterator[CodingAgentUpdate]:
        tool_names: dict[str, str] = {}
        attribution = Attribution()
        options = ClaudeAgentOptions(
            tools={"type": "preset", "preset": "claude_code"},
            cwd=self.request.cwd,
            mcp_servers=self.request.mcp_servers,
            strict_mcp_config=True,
            permission_mode="auto",
            env=claude_settings_env(),
            setting_sources=self.request.setting_sources,
            system_prompt={
                "type": "preset",
                "preset": "claude_code",
                "append": self.request.system_prompt_append,
            },
            output_format=(
                {"type": "json_schema", "schema": self.request.output_schema}
                if self.request.output_schema
                else None
            ),
            include_partial_messages=self.request.include_partial_messages,
            skills=self.request.skills,
            stderr=self.request.stderr,
            model=self.request.model,
            max_turns=self.request.max_turns,
            max_budget_usd=self.request.max_budget_usd,
            effort=self.effort,
        )
        async for message in claude_query(prompt=self.request.prompt, options=options):
            if self.request.on_native_message is not None:
                self.request.on_native_message(message)
            if is_noisy_partial_message(message):
                continue
            events = message_events(message, tool_names, attribution=attribution)
            final = _final_from_message(message)
            yield CodingAgentUpdate(
                raw=message_log_record(message),
                events=events,
                final=final,
            )

    async def abort(self) -> None:
        # The Claude Agent SDK query loop is cancelled by cancelling the
        # consuming task (AgentService wraps it with asyncio.wait_for today).
        return None


def _final_from_message(message: Any) -> CodingAgentFinal | None:
    if not isinstance(message, ResultMessage) and type(message).__name__ != "ResultMessage":
        return None
    return CodingAgentFinal(
        is_error=bool(getattr(message, "is_error", False)),
        result=getattr(message, "result", None),
        structured_output=getattr(message, "structured_output", None),
        subtype=getattr(message, "subtype", None),
        session_id=getattr(message, "session_id", None),
        cost_usd=getattr(message, "total_cost_usd", None),
        num_turns=getattr(message, "num_turns", None),
    )


class ClaudeCodingAgent:
    source = "claude_agent_sdk"
    # SDK EffortLevel：low/medium/high/xhigh/max（xhigh/max 仅部分模型支持）。
    supported_efforts = frozenset({"low", "medium", "high", "xhigh", "max"})
    capabilities = CodingAgentCapabilities(
        supports_mcp=True,
        supports_native_json_schema=True,
        supports_skills=True,
        supports_subagents=True,
        supports_cost=True,
        supports_turn_count=True,
        supports_abort=False,
        supports_partial_messages=True,
    )

    def __init__(
        self,
        *,
        backend_key: str = "claude",
        model: str | None = None,
        effort: str | None = None,
    ) -> None:
        self.backend_key = backend_key
        self.display_name = "Claude"
        self.model = model
        self.effort = effort

    def start(self, request: CodingAgentRequest) -> CodingAgentRun:
        if self.model is None or request.model is not None:
            return _ClaudeRun(request, effort=self.effort)
        return _ClaudeRun(request.with_model(self.model), effort=self.effort)
