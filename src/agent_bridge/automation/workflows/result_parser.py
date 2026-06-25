from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_bridge.core.domain import ValidationError

logger = logging.getLogger(__name__)


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
    task_version: str = ""
    reason: str | None = None
    artifacts: list[ParsedArtifact] = field(default_factory=list)


def _inside(parent: Path, child: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def parse_workflow_result(run_dir: Path) -> ParsedWorkflowResult:
    """解析 workflow run 产出的 result.json，校验状态与 task_key 契约。

    约束（服务端强制）：completed 必须带 task_key 与至少一个 artifact，artifact
    文件不得逃逸出 run 目录、格式仅限 markdown。任何一项不满足即抛 ValidationError。
    """
    result_path = run_dir / "out" / "result.json"
    if not result_path.exists():
        logger.warning("Workflow result.json 缺失 run_dir=%s", run_dir)
        raise ValidationError("workflow result.json not found")
    logger.info("Workflow result 解析开始 run_dir=%s", run_dir)
    try:
        raw = json.loads(result_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.warning("Workflow result.json 非法 JSON run_dir=%s 原因=%s", run_dir, exc)
        raise ValidationError("workflow result.json is invalid JSON") from exc
    if not isinstance(raw, dict):
        logger.warning("Workflow result.json 非对象 run_dir=%s", run_dir)
        raise ValidationError("workflow result.json must be an object")

    status = str(raw.get("status") or "")
    if status == "no_executable_task":
        logger.info("Workflow result 无可执行任务 run_dir=%s reason=%s", run_dir, raw.get("reason") or "")
        return ParsedWorkflowResult(status=status, reason=str(raw.get("reason") or ""))
    if status != "completed":
        logger.warning("Workflow result 状态不支持 run_dir=%s 状态=%s", run_dir, status)
        raise ValidationError("workflow result status is unsupported")

    task_key = str(raw.get("task_key") or "").strip()
    if not task_key:
        logger.warning("Workflow result 缺少 task_key run_dir=%s", run_dir)
        raise ValidationError("completed workflow result requires task_key")
    task_version = str(raw.get("task_version") or "")
    artifacts_raw = raw.get("artifacts")
    if not isinstance(artifacts_raw, list) or not artifacts_raw:
        logger.warning("Workflow result 缺少 artifacts run_dir=%s task=%s", run_dir, task_key)
        raise ValidationError("completed workflow result requires artifacts")

    artifacts: list[ParsedArtifact] = []
    for item in artifacts_raw:
        if not isinstance(item, dict):
            logger.warning("Workflow artifact 非对象 run_dir=%s task=%s", run_dir, task_key)
            raise ValidationError("artifact must be an object")
        artifact_file = run_dir / str(item.get("file") or "")
        if not _inside(run_dir, artifact_file):
            logger.warning("Workflow artifact 越界 run_dir=%s task=%s file=%s", run_dir, task_key, item.get("file"))
            raise ValidationError("artifact file escapes run directory")
        if not artifact_file.exists():
            logger.warning("Workflow artifact 文件缺失 run_dir=%s task=%s file=%s", run_dir, task_key, artifact_file)
            raise ValidationError("artifact file not found")
        tags = item.get("tags") or []
        if not isinstance(tags, list):
            logger.warning("Workflow artifact tags 非列表 run_dir=%s task=%s", run_dir, task_key)
            raise ValidationError("artifact tags must be a list")
        metadata = item.get("metadata") or {}
        if not isinstance(metadata, dict):
            logger.warning("Workflow artifact metadata 非对象 run_dir=%s task=%s", run_dir, task_key)
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
    logger.info(
        "Workflow result 解析完成 run_dir=%s task=%s 产物数=%d",
        run_dir,
        task_key,
        len(artifacts),
    )
    return ParsedWorkflowResult(
        status=status,
        task_key=task_key,
        task_version=task_version,
        artifacts=artifacts,
    )
