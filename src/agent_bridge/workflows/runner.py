from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


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
    (run_dir / "workflow-system-prompt.md").write_text(
        "\n".join(
            [
                "You are running an Agent Bridge workflow.",
                "Use the configured MCP tools to get or create tasks, log progress, and produce output.",
                "Write the final machine-readable result to ./out/result.json.",
            ]
        ),
        encoding="utf-8",
    )
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


class ClaudeWorkflowRunner:
    def run(self, base_dir: Path, spec: WorkflowRunSpec) -> WorkflowProcessResult:
        run_dir = prepare_run_directory(base_dir, spec)
        stdout_path = run_dir / "stdout.log"
        stderr_path = run_dir / "stderr.log"
        prompt = "Run the workflow defined in ./workflow.js and write the final result to ./out/result.json."
        started = time.monotonic()
        with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
            completed = subprocess.run(
                [
                    "claude",
                    "-p",
                    "--permission-mode",
                    "auto",
                    "--mcp-config",
                    "./.mcp.json",
                    "--append-system-prompt-file",
                    "./workflow-system-prompt.md",
                    prompt,
                ],
                cwd=run_dir,
                stdout=stdout,
                stderr=stderr,
                text=True,
                check=False,
            )
        return WorkflowProcessResult(
            run_dir=run_dir,
            exit_code=completed.returncode,
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
