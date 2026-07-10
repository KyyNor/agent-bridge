"""AgentService: a general-purpose coding-agent runner.

Wraps a pluggable coding-agent adapter with Agent Bridge conventions: an
isolated per-run working directory, optional profile-driven MCP access and
guidance, optional workflow context, and optional JSON-Schema structured output.
Results are returned as a uniform :class:`AgentRunResult` envelope — failures
are reported via ``ok=False`` and never raised.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from agent_bridge.agent_runtime.events import event_record, write_event
from agent_bridge.agent_runtime.registry import CodingAgentRegistry, create_coding_agent_registry
from agent_bridge.agent_runtime.support import build_agent_bridge_server_config, write_run_mcp_json
from agent_bridge.capability_hub.profiles.docs import install_profile_to_cwd
from agent_bridge.agent_runtime.types import CodingAgent, CodingAgentFinal, CodingAgentRequest
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
    run_key: str | None = None
    duration_ms: int = 0
    cost_usd: float | None = None
    num_turns: int | None = None


class AgentService:
    """Runs coding-agent queries with Agent Bridge profile/workflow wiring."""

    def __init__(
        self,
        *,
        paths: Any,
        store: Any,
        admins: set[str],
        governance: Any,
        coding_agent: CodingAgent | None = None,
        coding_agents: CodingAgentRegistry | None = None,
        mcp_url: str | None = None,
    ) -> None:
        self.paths = paths
        self.store = store
        self.admins = admins
        self.governance = governance
        if coding_agents is not None and coding_agent is not None:
            raise ValueError("pass either coding_agent or coding_agents, not both")
        if coding_agents is not None:
            self.coding_agents = coding_agents
        elif coding_agent is not None:
            self.coding_agents = CodingAgentRegistry(
                default_backend=coding_agent.backend_key,
                agents=[coding_agent],
            )
        else:
            self.coding_agents = create_coding_agent_registry()
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
        backend_key: str | None = None,
        timeout: float | None = None,
    ) -> AgentRunResult:
        """Run a one-shot coding-agent query and return a uniform result.

        Two modes:

        * **Managed** (default, ``cwd`` is None): an isolated working directory
          is created under ``run/agent-runs/{agent_name}_{uuid7}/``, ``files``
          are staged into it, and — when ``profile`` is set — the profile's
          CLAUDE.md guidance and governed ``.mcp.json`` are installed.

        * **In-place** (``cwd`` given): the caller owns the directory (including
          any agent guidance / staged files); this method just runs the adapter
          loop against it. Used by the workflow runner and other callers that
          prepare their own run directory. MCP is opt-in via ``mcp_servers``
          (defaults to none) so analyzing a repo with its own ``.mcp.json``
          does not accidentally wire up those servers.

        ``on_message`` (if given) is invoked with every streamed native message
        before the final result is captured, enabling progress/event logging.
        Failures (query errors, timeouts, profile-not-found, ...) return
        ``ok=False`` rather than raising.
        """
        timeout_seconds = timeout if timeout is not None else DEFAULT_TIMEOUT_SECONDS
        started = time.monotonic()
        started_iso = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        work_dir: Path | None = None
        events: list[dict[str, Any]] = []
        result_msg: CodingAgentFinal | None = None
        error: str | None = None
        mode = "in-place" if cwd is not None else "managed"
        effective_backend_key = backend_key or self.coding_agents.default_backend
        logger.info(
            "Agent run 开始 agent=%s backend=%s mode=%s profile=%s skills=%s",
            agent_name or "agent",
            effective_backend_key,
            mode,
            profile,
            ",".join(skills) if skills else "",
        )

        # Reserve the agent_runs row up front (status=running) so the run is
        # observable while in flight, and capture its run_key for the caller.
        run_key = new_run_id(agent_name or "agent")
        try:
            self.store.agent_runs.create(
                run_key=run_key,
                agent_name=agent_name or "agent",
                backend_key=effective_backend_key,
                profile_key=profile,
                workflow_key=workflow_key or None,
                workflow_run_id=run_id if workflow_key else None,
                cwd=None,  # backfilled below once the work dir is known
                model=model,
                status="running",
                prompt=prompt,
                output_schema=output_schema,
                started_at=started_iso,
            )
        except Exception:
            logger.error("Agent run 占位记录创建失败 run_key=%s", run_key, exc_info=True)

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
            self._record_cwd(run_key, work_dir)
            coding_agent = self.coding_agents.get(effective_backend_key)

            request = CodingAgentRequest(
                prompt=prompt,
                cwd=work_dir,
                mcp_servers=effective_mcp_servers,
                setting_sources=effective_setting_sources,
                output_schema=output_schema,
                system_prompt_append=self._system_prompt_append(output_schema, system_prompt_append),
                include_partial_messages=include_partial_messages,
                skills=skills,
                stderr=stderr,
                model=model,
                max_turns=max_turns,
                max_budget_usd=max_budget_usd,
                on_native_message=on_message,
            )
            result_msg = await asyncio.wait_for(
                self._drain_agent(coding_agent, request, work_dir, events),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            error = f"agent timed out after {timeout_seconds}s"
            logger.warning("Agent run 超时 agent=%s 超时=%ss", agent_name or "agent", timeout_seconds)
            error_event = event_record("error", status="failed", message=error)
            events.append(error_event)
            self._append_live_event(work_dir, error_event)
        except Exception as exc:
            logger.error("Agent run 失败 agent=%s 原因=%s", agent_name or "agent", exc, exc_info=True)
            error = f"{type(exc).__name__}: {exc}"
            error_event = event_record("error", status="failed", message=error)
            events.append(error_event)
            self._append_live_event(work_dir, error_event)

        result = self._build_result(work_dir, started, result_msg, output_schema, error)
        result.run_key = run_key
        logger.info(
            "Agent run 完成 agent=%s 成功=%s 耗时=%dms",
            agent_name or "agent",
            result.ok,
            result.duration_ms,
        )
        self._finish_run(
            run_key=run_key,
            started_iso=started_iso,
            result=result,
            events=events,
        )
        return result

    async def _drain_agent(
        self,
        coding_agent: CodingAgent,
        request: CodingAgentRequest,
        work_dir: Path | None,
        events: list[dict[str, Any]],
    ) -> CodingAgentFinal | None:
        last: CodingAgentFinal | None = None
        # Persist every raw native message to messages.jsonl in the work dir so the
        # run is self-contained: subagent transcript discovery, debugging, and
        # the unified event replay all read from here. This replaces the per-
        # caller on_message file writing the workflow runner used to do.
        # events.jsonl is the live event stream (one record per semantic event)
        # so progress polling can read it in real time, before the run finishes
        # and the full event list is flushed to the agent_runs row.
        raw_log = None
        event_log = None
        if work_dir is not None:
            try:
                raw_log = (work_dir / "messages.jsonl").open("a", encoding="utf-8")
                event_log = (work_dir / "events.jsonl").open("a", encoding="utf-8")
            except OSError:
                raw_log = None
                event_log = None
        run = None
        try:
            run = coding_agent.start(request)
            async for update in run.updates():
                if raw_log is not None and update.raw is not None:
                    raw_log.write(json.dumps(update.raw, ensure_ascii=False) + "\n")
                    raw_log.flush()
                if event_log is not None:
                    for record in update.events:
                        write_event(event_log, record)
                events.extend(update.events)
                if update.final is not None:
                    last = update.final
        except asyncio.CancelledError:
            if run is not None:
                await run.abort()
            raise
        finally:
            if raw_log is not None:
                raw_log.close()
            if event_log is not None:
                event_log.close()
        return last

    def _build_result(
        self,
        work_dir: Path | None,
        started: float,
        result_msg: CodingAgentFinal | None,
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
            "cost_usd": result_msg.cost_usd,
            "num_turns": result_msg.num_turns,
        }
        if result_msg.is_error:
            return AgentRunResult(
                ok=False, error=result_msg.result or result_msg.subtype, **meta
            )
        return AgentRunResult(
            ok=True, result=_extract_result(result_msg, output_schema), **meta
        )

    def _record_cwd(self, run_key: str, work_dir: Path | None) -> None:
        """Backfill the work-dir path on the placeholder row once it is known."""
        if work_dir is None:
            return
        try:
            self.store.agent_runs.update_cwd(run_key, str(work_dir))
        except Exception:
            logger.error("Agent run cwd 回填失败 run_key=%s", run_key, exc_info=True)

    def _append_live_event(self, work_dir: Path | None, record: dict[str, Any]) -> None:
        """Append a terminal event after the streaming loop has closed its file."""
        if work_dir is None:
            return
        try:
            with (work_dir / "events.jsonl").open("a", encoding="utf-8") as event_log:
                write_event(event_log, record)
        except OSError:
            logger.error("Agent run 实时事件补写失败 work_dir=%s", work_dir, exc_info=True)

    def _finish_run(
        self,
        *,
        run_key: str,
        started_iso: str,
        result: AgentRunResult,
        events: list[dict[str, Any]],
    ) -> None:
        """Update the placeholder row with the run's terminal outcome.

        Logging failures never break the run. Status maps from the result:
        success -> completed, failure -> failed."""
        status = "completed" if result.ok else "failed"
        finished_iso = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        try:
            self.store.agent_runs.finish_run(
                run_key,
                ok=result.ok,
                status=status,
                error=result.error,
                session_id=result.session_id,
                model=None,
                duration_ms=result.duration_ms,
                cost_usd=result.cost_usd,
                num_turns=result.num_turns,
                result=result.result,
                events=events,
                finished_at=finished_iso,
            )
        except Exception:
            logger.error("Agent run 结果回填失败 run_key=%s", run_key, exc_info=True)

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


def _extract_result(result_msg: Any, output_schema: dict[str, Any] | None) -> Any:
    """Extract the final value from an adapter final message.

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
