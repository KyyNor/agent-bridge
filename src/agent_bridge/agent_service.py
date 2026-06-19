"""AgentService: a general-purpose Claude Agent SDK runner.

Wraps ``claude_agent_sdk.query`` with Agent Bridge conventions: an isolated
per-run working directory, optional profile-driven MCP access and CLAUDE.md
guidance, optional workflow context, and optional JSON-Schema structured
output. Results are returned as a uniform :class:`AgentRunResult` envelope —
failures are reported via ``ok=False`` and never raised.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from claude_agent_sdk import ClaudeAgentOptions, query as claude_query
from claude_agent_sdk.types import ResultMessage

from agent_bridge.agent_events import (
    event_record,
    is_noisy_partial_message,
    message_events,
)
from agent_bridge.agent_support import build_agent_bridge_server_config, write_run_mcp_json
from agent_bridge.capabilities.profile_docs import install_profile_to_cwd
from agent_bridge.claude_agent import claude_settings_env
from agent_bridge.core.ids import new_run_id

logger = logging.getLogger(__name__)

DEFAULT_MCP_URL = "http://127.0.0.1:8765/mcp"
DEFAULT_TIMEOUT_SECONDS = 600.0


@dataclass
class AgentRunResult:
    """Uniform result envelope for :meth:`AgentService.run`.

    ``ok`` distinguishes success from failure; on success ``result`` is either
    a schema-conforming object (when ``output_schema`` was given) or the final
    assistant text. Failures populate ``error`` and never raise.
    """

    ok: bool
    result: Any | None = None
    error: str | None = None
    run_dir: str = ""
    session_id: str | None = None
    duration_ms: int = 0
    cost_usd: float | None = None
    num_turns: int | None = None


class AgentService:
    """Runs Claude Agent SDK queries with Agent Bridge profile/workflow wiring."""

    def __init__(
        self,
        *,
        paths: Any,
        store: Any,
        admins: set[str],
        governance: Any,
        mcp_url: str | None = None,
    ) -> None:
        self.paths = paths
        self.store = store
        self.admins = admins
        self.governance = governance
        self.mcp_url = mcp_url or DEFAULT_MCP_URL
        self.base_run_dir: Path = paths.run_dir / "agent-runs"

    async def run(
        self,
        *,
        prompt: str,
        agent_name: str | None = None,
        files: list[Path | str] | None = None,
        profile: str | None = None,
        workflow_key: str | None = None,
        run_id: str | None = None,
        output_schema: dict[str, Any] | None = None,
        system_prompt_append: str | None = None,
        cwd: Path | str | None = None,
        on_message: Callable[[Any], None] | None = None,
        skills: list[str] | None = None,
        setting_sources: list[str] | None = None,
        mcp_servers: Path | str | dict | None = None,
        stderr: Callable[[str], None] | None = None,
        include_partial_messages: bool = False,
        actor: str | None = None,
        model: str | None = None,
        max_turns: int | None = None,
        max_budget_usd: float | None = None,
        timeout: float | None = None,
    ) -> AgentRunResult:
        """Run a one-shot Claude agent query and return a uniform result.

        Two modes:

        * **Managed** (default, ``cwd`` is None): an isolated working directory
          is created under ``run/agent-runs/{agent_name}_{uuid7}/``, ``files``
          are staged into it, and — when ``profile`` is set — the profile's
          CLAUDE.md guidance and governed ``.mcp.json`` are installed.

        * **In-place** (``cwd`` given): the caller owns the directory (including
          any CLAUDE.md / staged files); this method just runs the SDK loop
          against it. Used by the workflow runner and other callers that
          prepare their own run directory. MCP is opt-in via ``mcp_servers``
          (defaults to none) so analyzing a repo with its own ``.mcp.json``
          does not accidentally wire up those servers.

        ``on_message`` (if given) is invoked with every streamed SDK message
        before the final result is captured, enabling progress/event logging.
        Failures (query errors, timeouts, profile-not-found, ...) return
        ``ok=False`` rather than raising.
        """
        timeout_seconds = timeout if timeout is not None else DEFAULT_TIMEOUT_SECONDS
        started = time.monotonic()
        work_dir: Path | None = None
        events: list[dict[str, Any]] = []
        tool_names: dict[str, str] = {}
        result_msg: ResultMessage | None = None
        error: str | None = None

        try:
            if cwd is not None:
                work_dir = Path(cwd)
                effective_setting_sources = setting_sources or []
                effective_mcp_servers: Any = mcp_servers if mcp_servers is not None else {}
            else:
                work_dir = self._make_work_dir(agent_name or "agent")
                self._stage_files(work_dir, files)
                if profile:
                    rendered = self.governance.render_profile_markdown(
                        actor or self._default_actor(), profile
                    )
                    install_profile_to_cwd(work_dir, profile, rendered["markdown"])
                mcp_config = build_agent_bridge_server_config(
                    self.mcp_url, profile, workflow_key=workflow_key, run_id=run_id
                )
                write_run_mcp_json(work_dir / ".mcp.json", mcp_config)
                effective_setting_sources = setting_sources or (
                    ["project"] if profile else []
                )
                effective_mcp_servers = (
                    mcp_servers if mcp_servers is not None else work_dir / ".mcp.json"
                )

            options = ClaudeAgentOptions(
                tools={"type": "preset", "preset": "claude_code"},
                cwd=work_dir,
                mcp_servers=effective_mcp_servers,
                strict_mcp_config=True,
                permission_mode="auto",
                env=claude_settings_env(),
                setting_sources=effective_setting_sources,
                system_prompt={
                    "type": "preset",
                    "preset": "claude_code",
                    "append": self._system_prompt_append(output_schema, system_prompt_append),
                },
                output_format=(
                    {"type": "json_schema", "schema": output_schema}
                    if output_schema
                    else None
                ),
                include_partial_messages=include_partial_messages,
                skills=skills,
                stderr=stderr,
                model=model,
                max_turns=max_turns,
                max_budget_usd=max_budget_usd,
            )
            result_msg = await asyncio.wait_for(
                self._drain_query(prompt, options, on_message, events, tool_names),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            error = f"agent timed out after {timeout_seconds}s"
            events.append(event_record("error", status="failed", message=error))
        except Exception as exc:
            logger.exception("AgentService run failed agent=%s", agent_name)
            error = f"{type(exc).__name__}: {exc}"
            events.append(event_record("error", status="failed", message=error))

        result = self._build_result(work_dir, started, result_msg, output_schema, error)
        self._persist_run(
            prompt=prompt,
            output_schema=output_schema,
            agent_name=agent_name,
            profile=profile,
            workflow_key=workflow_key,
            run_id=run_id,
            model=model,
            work_dir=work_dir,
            result=result,
            events=events,
        )
        return result

    async def _drain_query(
        self,
        prompt: str,
        options: Any,
        on_message: Callable[[Any], None] | None,
        events: list[dict[str, Any]],
        tool_names: dict[str, str],
    ) -> ResultMessage | None:
        last: ResultMessage | None = None
        async for message in claude_query(prompt=prompt, options=options):
            if on_message is not None:
                on_message(message)
            if not is_noisy_partial_message(message):
                events.extend(message_events(message, tool_names))
            if isinstance(message, ResultMessage):
                last = message
        return last

    def _build_result(
        self,
        work_dir: Path | None,
        started: float,
        result_msg: ResultMessage | None,
        output_schema: dict[str, Any] | None,
        error: str | None,
    ) -> AgentRunResult:
        duration_ms = int((time.monotonic() - started) * 1000)
        run_dir = str(work_dir) if work_dir else ""
        if error is not None:
            return AgentRunResult(
                ok=False, error=error, run_dir=run_dir, duration_ms=duration_ms
            )
        if result_msg is None:
            return AgentRunResult(
                ok=False,
                error="agent produced no result message",
                run_dir=run_dir,
                duration_ms=duration_ms,
            )
        meta = {
            "run_dir": run_dir,
            "duration_ms": duration_ms,
            "session_id": result_msg.session_id,
            "cost_usd": result_msg.total_cost_usd,
            "num_turns": result_msg.num_turns,
        }
        if result_msg.is_error:
            return AgentRunResult(
                ok=False, error=result_msg.result or result_msg.subtype, **meta
            )
        return AgentRunResult(
            ok=True, result=_extract_result(result_msg, output_schema), **meta
        )

    def _persist_run(
        self,
        *,
        prompt: str,
        output_schema: dict[str, Any] | None,
        agent_name: str | None,
        profile: str | None,
        workflow_key: str | None,
        run_id: str | None,
        model: str | None,
        work_dir: Path | None,
        result: AgentRunResult,
        events: list[dict[str, Any]],
    ) -> None:
        """Record this run in the agent_runs log. Logging failures never break the run."""
        try:
            self.store.agent_runs.create(
                run_key=new_run_id(agent_name or "agent"),
                agent_name=agent_name or "agent",
                profile_key=profile,
                workflow_key=workflow_key or None,
                workflow_run_id=run_id if workflow_key else None,
                session_id=result.session_id,
                cwd=str(work_dir) if work_dir else None,
                model=model,
                ok=result.ok,
                error=result.error,
                duration_ms=result.duration_ms,
                cost_usd=result.cost_usd,
                num_turns=result.num_turns,
                prompt=prompt,
                output_schema=output_schema,
                result=result.result,
                events=events,
            )
        except Exception:
            logger.exception("Failed to persist agent_run log")

    def _make_work_dir(self, agent_name: str) -> Path:
        work_dir = self.base_run_dir / new_run_id(agent_name)
        work_dir.mkdir(parents=True, exist_ok=True)
        return work_dir

    def _stage_files(self, work_dir: Path, files: list[Path | str] | None) -> None:
        if not files:
            return
        for item in files:
            src = Path(item)
            if not src.exists():
                continue
            dst = work_dir / src.name
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)

    def _system_prompt_append(
        self, output_schema: dict[str, Any] | None, extra: str | None
    ) -> str:
        parts: list[str] = []
        if extra:
            parts.append(extra)
        if output_schema:
            parts.append(
                "Produce your final answer as JSON matching this JSON Schema exactly:\n"
                + json.dumps(output_schema, ensure_ascii=False)
            )
        return "\n\n".join(parts)

    def _default_actor(self) -> str:
        for admin in sorted(self.admins):
            return admin
        return "root"


def _extract_result(result_msg: ResultMessage, output_schema: dict[str, Any] | None) -> Any:
    """Extract the final value from a ResultMessage.

    Prefers native ``structured_output``; falls back to parsing JSON out of the
    result text when a schema was requested; otherwise returns the result text.
    """
    if not output_schema:
        return result_msg.result or ""
    if result_msg.structured_output is not None:
        return result_msg.structured_output
    parsed = _extract_json(result_msg.result or "")
    return parsed if parsed is not None else (result_msg.result or "")


def _extract_json(text: str) -> Any:
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
