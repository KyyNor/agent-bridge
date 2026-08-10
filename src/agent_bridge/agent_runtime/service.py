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
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft7Validator

from agent_bridge.agent_runtime.control import RunControlRegistry
from agent_bridge.agent_runtime.events import event_record
from agent_bridge.agent_runtime.live_events import LiveAgentRunEventHub
from agent_bridge.agent_runtime.json_schema import normalize_draft7_schema
from agent_bridge.agent_runtime.registry import CodingAgentRegistry, create_coding_agent_registry
from agent_bridge.agent_runtime.support import (
    build_agent_bridge_server_config,
    build_opencode_mcp_config,
    write_run_mcp_json,
)
from agent_bridge.capability_hub.profiles.docs import install_profile_to_cwd
from agent_bridge.core.timeutil import utc_iso
from agent_bridge.agent_runtime.types import CodingAgent, CodingAgentFinal, CodingAgentRequest
from agent_bridge.agent_runtime.trace import (
    StageTimer,
    ToolTimingTracker,
    externalize_event_payloads,
)
from agent_bridge.core.ids import new_run_id
from agent_bridge.core.domain import ConflictError

logger = logging.getLogger(__name__)

DEFAULT_MCP_URL = "http://127.0.0.1:8765/mcp"
DEFAULT_TIMEOUT_SECONDS = 600.0
STOPPED_ERROR = "运行已由用户停止"


class AgentRunStopped(Exception):
    """Internal signal used to settle a cancelled adapter run."""


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
    stopped: bool = False


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
        self.control_registry = RunControlRegistry()
        self.live_events = LiveAgentRunEventHub()
        self._client_run_key_lock = threading.Lock()
        self._event_id_lock = threading.Lock()
        self._next_event_ids: dict[str, int] = {}

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
        run_key: str | None = None,
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
        effective_backend_key = backend_key or self.coding_agents.default_backend
        started = time.monotonic()
        started_iso = utc_iso()
        total_timer = StageTimer(
            "run.total", agent_name=effective_backend_key, source="agent_runtime"
        )
        prepare_timer = StageTimer(
            "run.prepare", agent_name=effective_backend_key, source="agent_runtime"
        )
        backend_timer: StageTimer | None = None
        backend_status = "success"
        prepare_event_written = False
        work_dir: Path | None = None
        events: list[dict[str, Any]] = []
        result_msg: CodingAgentFinal | None = None
        error: str | None = None
        effective_output_schema = output_schema
        mode = "in-place" if cwd is not None else "managed"
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
        caller_run_key = run_key
        run_key = run_key or new_run_id(agent_name or "agent")
        workflow_run_id = run_id if workflow_key else None
        effective_actor = actor or self._default_actor()
        owner_group_key = self._resolve_run_owner_group(
            actor=effective_actor,
            profile_key=profile,
            workflow_run_id=workflow_run_id,
        )
        if caller_run_key is not None:
            with self._client_run_key_lock:
                if self.control_registry.is_active(run_key):
                    raise ConflictError("agent run key already exists")
                control = self.control_registry.register(run_key, workflow_run_id=workflow_run_id)
        else:
            control = self.control_registry.register(run_key, workflow_run_id=workflow_run_id)
        try:
            self.store.agent_runs.create(
                run_key=run_key,
                agent_name=agent_name or "agent",
                backend_key=effective_backend_key,
                profile_key=profile,
                workflow_key=workflow_key or None,
                workflow_run_id=workflow_run_id,
                cwd=None,  # backfilled below once the work dir is known
                model=model,
                status="running",
                prompt=prompt,
                output_schema=output_schema,
                started_at=started_iso,
                actor=effective_actor,
                owner_group_key=owner_group_key,
            )
        except sqlite3.IntegrityError as exc:
            self.control_registry.finish(run_key)
            if caller_run_key is not None:
                raise ConflictError("agent run key already exists") from exc
            logger.error("Agent run 占位记录创建失败 run_key=%s", run_key, exc_info=True)
        except Exception:
            logger.error("Agent run 占位记录创建失败 run_key=%s", run_key, exc_info=True)

        try:
            if output_schema is not None:
                effective_output_schema = normalize_draft7_schema(output_schema)
            coding_agent = self.coding_agents.get(effective_backend_key)
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
                    self.mcp_url,
                    profile,
                    actor=actor or self._default_actor(),
                    workflow_key=workflow_key,
                    run_id=run_id,
                )
                write_run_mcp_json(work_dir / ".mcp.json", mcp_config)
                effective_setting_sources = setting_sources or (
                    ["project"] if profile else []
                )
                effective_mcp_servers = (
                    mcp_servers if mcp_servers is not None else work_dir / ".mcp.json"
                )
                if getattr(coding_agent, "source", None) in {"opencode_cli", "opencode_server"}:
                    write_run_mcp_json(
                        work_dir / "opencode.json",
                        build_opencode_mcp_config(mcp_config),
                    )
            self._record_cwd(run_key, work_dir)
            prepare_event = prepare_timer.finish()
            events.append(prepare_event)
            self._append_live_event(run_key, work_dir, prepare_event)
            prepare_event_written = True
            backend_timer = StageTimer(
                "backend.run", agent_name=effective_backend_key, source="agent_runtime"
            )

            request = CodingAgentRequest(
                prompt=prompt,
                cwd=work_dir,
                mcp_servers=effective_mcp_servers,
                setting_sources=effective_setting_sources,
                output_schema=effective_output_schema,
                system_prompt_append=self._system_prompt_append(effective_output_schema, system_prompt_append),
                include_partial_messages=include_partial_messages,
                skills=skills,
                stderr=stderr,
                model=model,
                max_turns=max_turns,
                max_budget_usd=max_budget_usd,
                on_native_message=on_message,
            )
            if self.control_registry.is_stop_requested(run_key):
                raise AgentRunStopped
            result_msg = await self._drain_agent_with_control(
                run_key=run_key,
                coding_agent=coding_agent,
                request=request,
                work_dir=work_dir,
                events=events,
                stop_requested=control.stop_requested,
                timeout=timeout_seconds,
            )
        except AgentRunStopped:
            backend_status = "stopped"
            error = STOPPED_ERROR
            stopped_event = event_record("status", status="stopped", message=STOPPED_ERROR)
            error_event = event_record("error", status="stopped", message=STOPPED_ERROR)
            events.extend((stopped_event, error_event))
            self._append_live_event(run_key, work_dir, stopped_event)
            self._append_live_event(run_key, work_dir, error_event)
            logger.info("Agent run 已由用户停止 agent=%s", agent_name or "agent")
        except TimeoutError:
            backend_status = "timeout"
            error = f"agent timed out after {timeout_seconds}s"
            logger.warning("Agent run 超时 agent=%s 超时=%ss", agent_name or "agent", timeout_seconds)
            error_event = event_record("error", status="failed", message=error)
            events.append(error_event)
            self._append_live_event(run_key, work_dir, error_event)
        except Exception as exc:
            backend_status = "failed"
            logger.error("Agent run 失败 agent=%s 原因=%s", agent_name or "agent", exc, exc_info=True)
            error = f"{type(exc).__name__}: {exc}"
            error_event = event_record("error", status="failed", message=error)
            events.append(error_event)
            self._append_live_event(run_key, work_dir, error_event)
        finally:
            if not prepare_event_written:
                prepare_event = prepare_timer.finish(
                    status="failed",
                    message=error,
                )
                events.append(prepare_event)
                self._append_live_event(run_key, work_dir, prepare_event)
            if backend_timer is not None:
                backend_event = backend_timer.finish(
                    status=backend_status,
                    message=error,
                )
                events.append(backend_event)
                self._append_live_event(run_key, work_dir, backend_event)

        finalize_timer = StageTimer(
            "run.finalize", agent_name=effective_backend_key, source="agent_runtime"
        )
        result = self._build_result(
            work_dir,
            started,
            result_msg,
            effective_output_schema,
            error,
            stopped=error == STOPPED_ERROR,
        )
        result.run_key = run_key
        finalize_event = finalize_timer.finish(
            status="success" if result.ok else ("stopped" if result.stopped else "failed"),
            message=result.error,
        )
        events.append(finalize_event)
        self._append_live_event(run_key, work_dir, finalize_event)
        total_event = total_timer.finish(
            status="success" if result.ok else ("stopped" if result.stopped else "failed"),
            message=result.error,
        )
        events.append(total_event)
        self._append_live_event(run_key, work_dir, total_event)
        logger.info(
            "Agent run 完成 agent=%s 成功=%s 耗时=%dms",
            agent_name or "agent",
            result.ok,
            result.duration_ms,
        )
        try:
            self._finish_run(
                run_key=run_key,
                started_iso=started_iso,
                result=result,
                events=events,
            )
        finally:
            terminal_status = "stopped" if result.stopped else ("completed" if result.ok else "failed")
            self.live_events.publish_terminal(
                run_key,
                {
                    "run_key": run_key,
                    "status": terminal_status,
                    "ok": result.ok,
                    "error": result.error,
                },
            )
            with self._event_id_lock:
                self._next_event_ids.pop(run_key, None)
            self.control_registry.finish(run_key)
        return result

    def request_stop(self, run_key: str) -> bool:
        return self.control_registry.request_stop(run_key)

    def has_pending_control(self, run_key: str) -> bool:
        return self.control_registry.has_pending_control(run_key)

    def has_active_control(self, run_key: str) -> bool:
        return self.control_registry.is_active(run_key)

    async def _drain_agent_with_control(
        self,
        run_key: str,
        coding_agent: CodingAgent,
        request: CodingAgentRequest,
        work_dir: Path | None,
        events: list[dict[str, Any]],
        stop_requested: Any,
        timeout: float,
    ) -> CodingAgentFinal | None:
        if stop_requested.is_set():
            raise AgentRunStopped
        run_task = asyncio.create_task(self._drain_agent(run_key, coding_agent, request, work_dir, events))
        stop_task = asyncio.create_task(_wait_for_thread_event(stop_requested))
        try:
            done, _ = await asyncio.wait(
                {run_task, stop_task}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
            if stop_task in done:
                run_task.cancel()
                await _await_cancelled_task(run_task)
                raise AgentRunStopped
            if run_task in done:
                stop_task.cancel()
                await _await_cancelled_task(stop_task)
                return run_task.result()
            run_task.cancel()
            stop_task.cancel()
            await _await_cancelled_task(run_task)
            await _await_cancelled_task(stop_task)
            raise TimeoutError(f"agent timed out after {timeout}s")
        finally:
            if not run_task.done():
                run_task.cancel()
                await _await_cancelled_task(run_task)
            if not stop_task.done():
                stop_task.cancel()
                await _await_cancelled_task(stop_task)

    async def _drain_agent(
        self,
        run_key: str,
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
        timing = ToolTimingTracker()
        if work_dir is not None:
            try:
                raw_log = (work_dir / "messages.jsonl").open("a", encoding="utf-8")
            except OSError:
                raw_log = None
        run = None
        try:
            run = coding_agent.start(request)
            async for update in run.updates():
                if raw_log is not None and update.raw is not None:
                    raw_log.write(json.dumps(update.raw, ensure_ascii=False) + "\n")
                    raw_log.flush()
                normalized_events = timing.apply(update.events)
                normalized_events = [
                    externalize_event_payloads(record, work_dir)
                    for record in normalized_events
                ]
                for record in normalized_events:
                    self._append_live_event(run_key, work_dir, record)
                events.extend(normalized_events)
                if update.final is not None:
                    last = update.final
        except asyncio.CancelledError:
            if run is not None:
                await run.abort()
            raise
        finally:
            pending_events = [
                externalize_event_payloads(record, work_dir)
                for record in timing.close_open()
            ]
            for record in pending_events:
                self._append_live_event(run_key, work_dir, record)
            events.extend(pending_events)
            if raw_log is not None:
                raw_log.close()
        return last

    def _build_result(
        self,
        work_dir: Path | None,
        started: float,
        result_msg: CodingAgentFinal | None,
        output_schema: dict[str, Any] | None,
        error: str | None,
        *,
        stopped: bool = False,
    ) -> AgentRunResult:
        duration_ms = int((time.monotonic() - started) * 1000)
        run_dir = str(work_dir) if work_dir else ""
        if error is not None:
            return AgentRunResult(
                ok=False, stopped=stopped, error=error, run_dir=run_dir, duration_ms=duration_ms
            )
        if result_msg is None:
            return AgentRunResult(
                ok=False,
                stopped=stopped,
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
                ok=False, stopped=stopped, error=result_msg.result or result_msg.subtype, **meta
            )
        result = _extract_result(result_msg, output_schema)
        schema_error = _output_schema_error(output_schema, result)
        if schema_error is not None:
            return AgentRunResult(ok=False, stopped=stopped, error=schema_error, **meta)
        return AgentRunResult(ok=True, stopped=stopped, result=result, **meta)

    def _record_cwd(self, run_key: str, work_dir: Path | None) -> None:
        """Backfill the work-dir path on the placeholder row once it is known."""
        if work_dir is None:
            return
        try:
            self.store.agent_runs.update_cwd(run_key, str(work_dir))
        except Exception:
            logger.error("Agent run cwd 回填失败 run_key=%s", run_key, exc_info=True)

    def _append_live_event(self, run_key: str, work_dir: Path | None, record: dict[str, Any]) -> None:
        """先写入运行 JSONL，再将新事件发布给本进程 SSE 订阅者。"""
        if work_dir is None:
            return
        try:
            with (work_dir / "events.jsonl").open("a", encoding="utf-8") as event_log:
                event_id = self._next_event_id(run_key, work_dir)
                record["event_id"] = event_id
                event_log.write(json.dumps(record, ensure_ascii=False) + "\n")
                event_log.flush()
            self.live_events.publish_event(run_key, record)
        except OSError:
            logger.error("Agent run 实时事件补写失败 work_dir=%s", work_dir, exc_info=True)

    def _next_event_id(self, run_key: str, work_dir: Path) -> int:
        with self._event_id_lock:
            next_id = self._next_event_ids.get(run_key)
            if next_id is None:
                path = work_dir / "events.jsonl"
                try:
                    existing = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
                except OSError:
                    existing = 0
                next_id = existing + 1
            self._next_event_ids[run_key] = next_id + 1
            return next_id

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
        success -> completed, stopped -> stopped, failure -> failed."""
        status = "stopped" if result.stopped else ("completed" if result.ok else "failed")
        finished_iso = utc_iso()
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

    def _resolve_run_owner_group(
        self,
        *,
        actor: str,
        profile_key: str | None,
        workflow_run_id: str | None,
    ) -> str:
        access = getattr(self.governance, "access", None)
        if access is None:
            return ""
        if workflow_run_id:
            workflow_run = self.store.get_workflow_run(workflow_run_id)
            if workflow_run and workflow_run.get("owner_group_key"):
                return str(workflow_run["owner_group_key"])
        if profile_key:
            profile = self.store.get_project_profile(profile_key)
            if profile and profile.get("owner_group_key"):
                return str(profile["owner_group_key"])
        return str(access.actor_group_key(actor, required=True))


async def _wait_for_thread_event(stop_requested: Any) -> None:
    while not stop_requested.is_set():
        await asyncio.sleep(0.05)


async def _await_cancelled_task(task: asyncio.Task[Any]) -> None:
    try:
        await task
    except asyncio.CancelledError:
        pass


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


def _output_schema_error(output_schema: dict[str, Any] | None, result: Any) -> str | None:
    if output_schema is None:
        return None
    errors = sorted(
        Draft7Validator(output_schema).iter_errors(result),
        key=lambda item: (list(item.absolute_path), str(item.validator)),
    )
    if not errors:
        return None
    first = errors[0]
    path = ".".join(str(part) for part in first.absolute_path) or "<root>"
    return f"agent output_schema invalid field={path}: {first.message}"


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
