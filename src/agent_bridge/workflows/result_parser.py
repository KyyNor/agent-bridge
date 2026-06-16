from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_bridge.core.domain import ValidationError


@dataclass(frozen=True)
class ParsedArtifact:
    title: str
    path: str
    tags: list[str]
    format: str
    summary: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedWorkflowResult:
    status: str
    task_key: str | None = None
    reason: str | None = None
    artifacts: list[ParsedArtifact] = field(default_factory=list)


def _inside(parent: Path, child: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def parse_workflow_result(run_dir: Path) -> ParsedWorkflowResult:
    result_path = run_dir / "out" / "result.json"
    if not result_path.exists():
        raise ValidationError("workflow result.json not found")
    try:
        raw = json.loads(result_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError("workflow result.json is invalid JSON") from exc
    if not isinstance(raw, dict):
        raise ValidationError("workflow result.json must be an object")

    status = str(raw.get("status") or "")
    if status == "no_executable_task":
        return ParsedWorkflowResult(status=status, reason=str(raw.get("reason") or ""))
    if status != "completed":
        raise ValidationError("workflow result status is unsupported")

    task_key = str(raw.get("task_key") or "").strip()
    if not task_key:
        raise ValidationError("completed workflow result requires task_key")
    artifacts_raw = raw.get("artifacts")
    if not isinstance(artifacts_raw, list) or not artifacts_raw:
        raise ValidationError("completed workflow result requires artifacts")

    artifacts: list[ParsedArtifact] = []
    for item in artifacts_raw:
        if not isinstance(item, dict):
            raise ValidationError("artifact must be an object")
        artifact_file = run_dir / str(item.get("file") or "")
        if not _inside(run_dir, artifact_file):
            raise ValidationError("artifact file escapes run directory")
        if not artifact_file.exists():
            raise ValidationError("artifact file not found")
        tags = item.get("tags") or []
        if not isinstance(tags, list):
            raise ValidationError("artifact tags must be a list")
        metadata = item.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise ValidationError("artifact metadata must be an object")
        artifacts.append(
            ParsedArtifact(
                title=str(item.get("title") or "").strip(),
                path=str(item.get("path") or "").strip(),
                tags=[str(tag) for tag in tags],
                format=str(item.get("format") or "markdown"),
                summary=str(item.get("summary") or ""),
                content=artifact_file.read_text(encoding="utf-8"),
                metadata=metadata,
            )
        )
    return ParsedWorkflowResult(status=status, task_key=task_key, artifacts=artifacts)
