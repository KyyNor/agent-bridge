from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from agent_bridge.agent_runtime.support import build_agent_bridge_server_config, write_run_mcp_json

logger = logging.getLogger(__name__)


WORKFLOW_PROMPT = "Run the workflow defined in ./workflow.js and write the final result to ./out/result.json."
WORKFLOW_SYSTEM_PROMPT = "\n".join(
    [
        "You are running an Agent Bridge workflow.",
        "Use the configured MCP tools to get or create tasks, log progress, and produce output.",
        "Write the final machine-readable result to ./out/result.json.",
    ]
)


@dataclass(frozen=True)
class WorkflowRunSpec:
    run_id: str
    workflow_key: str
    profile_key: str
    workflow_js: str
    mcp_url: str
    timeout_seconds: float | None = None


@dataclass(frozen=True)
class WorkflowProcessResult:
    run_dir: Path
    exit_code: int
    stdout_path: Path
    stderr_path: Path
    duration_ms: int
    stopped: bool = False


class WorkflowRunner(Protocol):
    def run(self, base_dir: Path, spec: WorkflowRunSpec) -> WorkflowProcessResult: ...


def prepare_run_directory(base_dir: Path, spec: WorkflowRunSpec) -> Path:
    run_dir = base_dir / spec.run_id
    out_dir = run_dir / "out"
    run_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "workflow.js").write_text(spec.workflow_js, encoding="utf-8")
    (run_dir / "workflow-system-prompt.md").write_text(WORKFLOW_SYSTEM_PROMPT, encoding="utf-8")
    mcp_config = build_agent_bridge_server_config(
        spec.mcp_url,
        spec.profile_key,
        workflow_key=spec.workflow_key,
        run_id=spec.run_id,
    )
    write_run_mcp_json(run_dir / ".mcp.json", mcp_config)
    return run_dir


class ClaudeWorkflowRunner:
    """Runs a workflow via :class:`AgentService` (the single SDK entry point)."""

    def __init__(self, agent_service: Any) -> None:
        self._agent_service = agent_service

    def run(self, base_dir: Path, spec: WorkflowRunSpec) -> WorkflowProcessResult:
        """Drive a workflow run: prepare an isolated work dir, run AgentService.

        AgentService now persists raw SDK messages (``messages.jsonl``) and the
        unified event stream (``agent_runs.events_json``) itself, so this runner
        no longer writes ``events.jsonl``/``stdout.log``. Only process-level
        stderr (SDK error output) is still captured here for diagnostics.
        """
        logger.info(
            "Workflow agent 调用开始 workflow=%s run=%s profile=%s",
            spec.workflow_key,
            spec.run_id,
            spec.profile_key,
        )
        run_dir = prepare_run_directory(base_dir, spec)
        stderr_path = run_dir / "stderr.log"
        started = time.monotonic()
        with stderr_path.open("w", encoding="utf-8") as stderr:
            def write_stderr(chunk: str) -> None:
                stderr.write(chunk)
                stderr.flush()

            res = asyncio.run(
                self._agent_service.run(
                    prompt=WORKFLOW_PROMPT,
                    agent_name="workflow",
                    cwd=run_dir,
                    mcp_servers=run_dir / ".mcp.json",
                    system_prompt_append=WORKFLOW_SYSTEM_PROMPT,
                    # Pass through the workflow identity so the agent_runs row
                    # produced by AgentService can be looked up via
                    # ``?workflow_run_id=`` (and ``?workflow_key=``).
                    workflow_key=spec.workflow_key,
                    run_id=spec.run_id,
                    stderr=write_stderr,
                    include_partial_messages=True,
                    setting_sources=[],
                    timeout=spec.timeout_seconds,
                )
            )
            exit_code = 0 if res.ok else 1
            if not res.ok:
                stderr.write(f"{res.error or 'unknown error'}\n")
        logger.info(
            "Workflow agent 调用完成 workflow=%s run=%s exit_code=%d 耗时=%dms",
            spec.workflow_key,
            spec.run_id,
            exit_code,
            int((time.monotonic() - started) * 1000),
        )
        return WorkflowProcessResult(
            run_dir=run_dir,
            exit_code=exit_code,
            stdout_path=stderr_path,  # raw messages now live in messages.jsonl; kept for compat
            stderr_path=stderr_path,
            duration_ms=int((time.monotonic() - started) * 1000),
            stopped=bool(getattr(res, "stopped", False)),
        )


class FakeWorkflowRunner:
    def __init__(self, status: str = "no_executable_task") -> None:
        self.status = status

    def run(self, base_dir: Path, spec: WorkflowRunSpec) -> WorkflowProcessResult:
        run_dir = prepare_run_directory(base_dir, spec)
        result_path = run_dir / "out" / "result.json"
        if self.status == "completed":
            artifact_dir = run_dir / "out" / "artifacts"
            artifact_dir.mkdir(parents=True, exist_ok=True)
            (artifact_dir / "report.md").write_text("# Report", encoding="utf-8")
            result = {
                "status": "completed",
                "task_key": "fake-task",
                "artifacts": [
                    {
                        "title": "Fake Report",
                        "path": "reports/fake/index.md",
                        "tags": ["fake"],
                        "format": "markdown",
                        "file": "out/artifacts/report.md",
                        "summary": "Fake report",
                    }
                ],
            }
        else:
            result = {"status": "no_executable_task", "reason": "fake runner"}
        result_path.write_text(json.dumps(result), encoding="utf-8")
        stdout_path = run_dir / "stdout.log"
        stderr_path = run_dir / "stderr.log"
        stdout_path.write_text("", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
        return WorkflowProcessResult(
            run_dir=run_dir,
            exit_code=0,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            duration_ms=1,
        )
