from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, TextIO

from agent_bridge.agent_support import build_agent_bridge_server_config, write_run_mcp_json


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
    mcp_config = build_agent_bridge_server_config(
        spec.mcp_url,
        spec.profile_key,
        workflow_key=spec.workflow_key,
        run_id=spec.run_id,
    )
    write_run_mcp_json(run_dir / ".mcp.json", mcp_config)
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


def _event_record(kind: str, **values: Any) -> dict[str, Any]:
    return {
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "agent_name": "claude",
        "source": "claude_agent_sdk",
        "kind": kind,
        **{key: _json_safe(value) for key, value in values.items() if value is not None},
    }


def _write_event(events: TextIO, record: dict[str, Any]) -> None:
    events.write(json.dumps(record, ensure_ascii=False) + "\n")
    events.flush()


def _is_noisy_partial_message(message: Any) -> bool:
    return getattr(message, "subtype", None) in {"thinking_tokens", "task_progress"}


def _message_events(message: Any, tool_names: dict[str, str]) -> list[dict[str, Any]]:
    session_id = getattr(message, "session_id", None)
    message_type = type(message).__name__
    if message_type == "ResultMessage":
        status = "failed" if getattr(message, "is_error", False) else "success"
        result = getattr(message, "result", None) or getattr(message, "subtype", "")
        return [
            _event_record(
                "result",
                status=status,
                message=result,
                session_id=session_id,
                total_cost_usd=getattr(message, "total_cost_usd", None),
                num_turns=getattr(message, "num_turns", None),
            )
        ]

    records: list[dict[str, Any]] = []
    content = getattr(message, "content", None)
    if isinstance(content, list):
        for block in content:
            block_type = type(block).__name__
            if block_type == "TextBlock":
                text = str(getattr(block, "text", "")).strip()
                if text:
                    records.append(_event_record("agent_message", message=text, session_id=session_id))
            elif block_type in {"ToolUseBlock", "ServerToolUseBlock"}:
                tool_id = str(getattr(block, "id", "") or getattr(block, "tool_use_id", ""))
                tool_name = str(getattr(block, "name", "") or "unknown")
                if tool_id:
                    tool_names[tool_id] = tool_name
                records.append(
                    _event_record(
                        "tool_call",
                        status="started",
                        tool_name=tool_name,
                        tool_use_id=tool_id,
                        message=f"调用工具 {tool_name}",
                        session_id=session_id,
                    )
                )
            elif block_type in {"ToolResultBlock", "ServerToolResultBlock"}:
                tool_id = str(getattr(block, "tool_use_id", "") or "")
                tool_name = tool_names.get(tool_id, tool_id or "unknown")
                status = "failed" if getattr(block, "is_error", False) else "success"
                records.append(
                    _event_record(
                        "tool_result",
                        status=status,
                        tool_name=tool_name,
                        tool_use_id=tool_id,
                        message=f"工具 {tool_name} 调用{'失败' if status == 'failed' else '成功'}",
                        session_id=session_id,
                    )
                )
    elif isinstance(content, str) and content.strip():
        records.append(_event_record("agent_message", message=content.strip(), session_id=session_id))

    if records:
        return records

    subtype = getattr(message, "subtype", None)
    if _is_noisy_partial_message(message):
        # High-frequency SDK partials are noisy and not useful in the run event
        # log, so drop them instead of recording status events.
        return []
    if subtype:
        return [
            _event_record(
                "status",
                status=str(subtype),
                message=str(subtype),
                session_id=session_id,
            )
        ]
    return []


class ClaudeWorkflowRunner:
    """Runs a workflow via :class:`AgentService` (the single SDK entry point)."""

    def __init__(self, agent_service: Any) -> None:
        self._agent_service = agent_service

    def run(self, base_dir: Path, spec: WorkflowRunSpec) -> WorkflowProcessResult:
        run_dir = prepare_run_directory(base_dir, spec)
        stdout_path = run_dir / "stdout.log"
        stderr_path = run_dir / "stderr.log"
        events_path = run_dir / "events.jsonl"
        started = time.monotonic()
        with (
            stdout_path.open("w", encoding="utf-8") as stdout,
            stderr_path.open("w", encoding="utf-8") as stderr,
            events_path.open("w", encoding="utf-8") as events,
        ):
            tool_names: dict[str, str] = {}

            def on_message(message: Any) -> None:
                if _is_noisy_partial_message(message):
                    # Skip noisy streaming partials entirely so they never reach
                    # the run logs (stdout.log) or event stream (events.jsonl).
                    return
                stdout.write(json.dumps(_message_log_record(message), ensure_ascii=False) + "\n")
                stdout.flush()
                for record in _message_events(message, tool_names):
                    _write_event(events, record)

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
                    on_message=on_message,
                    stderr=write_stderr,
                    include_partial_messages=True,
                    setting_sources=[],
                )
            )
            if res.ok:
                exit_code = 0
            else:
                exit_code = 1
                error_message = res.error or "unknown error"
                stderr.write(f"{error_message}\n")
                _write_event(events, _event_record("error", status="failed", message=error_message))
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
