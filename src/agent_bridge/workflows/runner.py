from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TextIO

from claude_agent_sdk import ClaudeAgentOptions, query as claude_query

from agent_bridge.claude_agent import claude_settings_env


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


@dataclass(frozen=True)
class WorkflowProcessResult:
    run_dir: Path
    exit_code: int
    stdout_path: Path
    stderr_path: Path
    duration_ms: int


class WorkflowRunner(Protocol):
    def run(self, base_dir: Path, spec: WorkflowRunSpec) -> WorkflowProcessResult: ...


def prepare_run_directory(base_dir: Path, spec: WorkflowRunSpec) -> Path:
    run_dir = base_dir / spec.run_id
    out_dir = run_dir / "out"
    run_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "workflow.js").write_text(spec.workflow_js, encoding="utf-8")
    (run_dir / "workflow-system-prompt.md").write_text(WORKFLOW_SYSTEM_PROMPT, encoding="utf-8")
    mcp_config = {
        "mcpServers": {
            "agent-bridge": {
                "type": "http",
                "url": spec.mcp_url,
                "headers": {
                    "X-Agent-Bridge-MetaMCP-Profile": spec.profile_key,
                    "X-Agent-Bridge-Workflow": "true",
                    "X-Agent-Bridge-Workflow-Key": spec.workflow_key,
                    "X-Agent-Bridge-Workflow-Run-Id": spec.run_id,
                },
            }
        }
    }
    (run_dir / ".mcp.json").write_text(json.dumps(mcp_config, ensure_ascii=False, indent=2), encoding="utf-8")
    return run_dir


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return str(value)


def _message_log_record(message: Any) -> dict[str, Any]:
    record: dict[str, Any] = {"type": type(message).__name__}
    for attr in ("subtype", "session_id", "uuid", "result", "total_cost_usd", "duration_ms", "num_turns"):
        if hasattr(message, attr):
            record[attr] = _json_safe(getattr(message, attr))
    if hasattr(message, "content"):
        record["content"] = _json_safe(getattr(message, "content"))
    return record


async def _run_claude_agent_sdk(run_dir: Path, stdout: TextIO, stderr: TextIO) -> None:
    def write_stderr(chunk: str) -> None:
        stderr.write(chunk)
        stderr.flush()

    options = ClaudeAgentOptions(
        tools={"type": "preset", "preset": "claude_code"},
        cwd=run_dir,
        mcp_servers=run_dir / ".mcp.json",
        strict_mcp_config=True,
        permission_mode="bypassPermissions",
        env=claude_settings_env(),
        setting_sources=[],
        system_prompt={
            "type": "preset",
            "preset": "claude_code",
            "append": WORKFLOW_SYSTEM_PROMPT,
        },
        include_partial_messages=True,
        stderr=write_stderr,
    )
    async for message in claude_query(prompt=WORKFLOW_PROMPT, options=options):
        stdout.write(json.dumps(_message_log_record(message), ensure_ascii=False) + "\n")
        stdout.flush()


class ClaudeWorkflowRunner:
    def run(self, base_dir: Path, spec: WorkflowRunSpec) -> WorkflowProcessResult:
        run_dir = prepare_run_directory(base_dir, spec)
        stdout_path = run_dir / "stdout.log"
        stderr_path = run_dir / "stderr.log"
        started = time.monotonic()
        with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
            try:
                asyncio.run(_run_claude_agent_sdk(run_dir, stdout, stderr))
                exit_code = 0
            except Exception as exc:
                exit_code = 1
                stderr.write(f"{type(exc).__name__}: {exc}\n")
        return WorkflowProcessResult(
            run_dir=run_dir,
            exit_code=exit_code,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            duration_ms=int((time.monotonic() - started) * 1000),
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
