from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


_LAUNCH_RE = re.compile(
    r"Task ID:\s*(?P<task_id>[^\n]+).*?Transcript dir:\s*(?P<transcript_dir>[^\n]+).*?(?:Run ID:\s*(?P<run_id>[^\n]+))?",
    re.DOTALL,
)
_TASK_ID_TAG_RE = re.compile(r"<task_id>\s*(?P<task_id>.*?)\s*</task_id>", re.DOTALL)
_STATUS_TAG_RE = re.compile(r"<status>\s*(?P<status>.*?)\s*</status>", re.DOTALL)
_OUTPUT_TAG_RE = re.compile(r"<output>\s*(?P<output>.*?)\s*</output>", re.DOTALL)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _stdout_blocks(run_dir: Path) -> list[str]:
    stdout_path = run_dir / "stdout.log"
    if not stdout_path.is_file():
        return []
    blocks: list[str] = []
    for row in _read_jsonl(stdout_path):
        content = row.get("content")
        if isinstance(content, list):
            blocks.extend(item for item in content if isinstance(item, str))
        elif isinstance(content, str):
            blocks.append(content)
    return blocks


def _normalise_block(block: str) -> str:
    return block.replace("\\n", "\n")


def _task_refs(run_dir: Path, task_id: str) -> dict[str, Any]:
    refs: dict[str, Any] = {"task_id": task_id}
    for block in _stdout_blocks(run_dir):
        text = _normalise_block(block)
        launch = _LAUNCH_RE.search(text)
        if launch and launch.group("task_id").strip() == task_id:
            refs["transcript_dir"] = launch.group("transcript_dir").strip()
            if launch.group("run_id"):
                refs["workflow_subrun_id"] = launch.group("run_id").strip()
        output_task_id = _TASK_ID_TAG_RE.search(text)
        output = _OUTPUT_TAG_RE.search(text)
        if output_task_id and output and output_task_id.group("task_id").strip() == task_id:
            status = _STATUS_TAG_RE.search(text)
            refs["task_output_status"] = status.group("status").strip() if status else None
            refs["task_output"] = output.group("output").strip()
    return refs


def _event_base(row: dict[str, Any], *, kind: str, role: str) -> dict[str, Any]:
    event: dict[str, Any] = {
        "kind": kind,
        "role": role,
    }
    if row.get("timestamp"):
        event["created_at"] = row["timestamp"]
    if row.get("uuid"):
        event["uuid"] = row["uuid"]
    agent_id = row.get("agentId")
    if isinstance(agent_id, str) and agent_id:
        event["agent_id"] = agent_id
    return event


def _append_usage(event: dict[str, Any], message: dict[str, Any]) -> None:
    usage = message.get("usage")
    if isinstance(usage, dict):
        event["usage"] = usage


def _message_events(row: dict[str, Any]) -> list[dict[str, Any]]:
    message = row.get("message")
    if not isinstance(message, dict):
        return []
    role = str(message.get("role") or row.get("type") or "")
    content = message.get("content")
    events: list[dict[str, Any]] = []

    if isinstance(content, str):
        event = _event_base(row, kind="prompt" if role == "user" else "text", role=role)
        event["content"] = content
        _append_usage(event, message)
        events.append(event)
        return events

    if not isinstance(content, list):
        return events

    for block in content:
        if isinstance(block, str):
            event = _event_base(row, kind="text", role=role)
            event["content"] = block
            _append_usage(event, message)
            events.append(event)
            continue
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "")
        if block_type == "thinking":
            event = _event_base(row, kind="thinking", role=role)
            event["content"] = str(block.get("thinking") or "")
        elif block_type == "text":
            event = _event_base(row, kind="text", role=role)
            event["content"] = str(block.get("text") or "")
        elif block_type == "tool_use":
            event = _event_base(row, kind="tool_call", role=role)
            event["tool_use_id"] = block.get("id")
            event["tool_name"] = block.get("name")
            event["input"] = block.get("input")
        elif block_type == "tool_result":
            event = _event_base(row, kind="tool_result", role=role)
            event["tool_use_id"] = block.get("tool_use_id")
            event["content"] = block.get("content")
            if block.get("is_error") is not None:
                event["is_error"] = block.get("is_error")
        else:
            event = _event_base(row, kind=block_type or "message", role=role)
            event["content"] = block
        _append_usage(event, message)
        events.append(event)
    return events


def _journal(transcript_dir: Path) -> tuple[list[str], dict[str, Any]]:
    order: list[str] = []
    results: dict[str, Any] = {}
    for row in _read_jsonl(transcript_dir / "journal.jsonl"):
        agent_id = row.get("agentId")
        if not isinstance(agent_id, str) or not agent_id:
            continue
        if row.get("type") == "started" and agent_id not in order:
            order.append(agent_id)
        if row.get("type") == "result":
            results[agent_id] = row.get("result")
    return order, results


def _agent_id_from_path(path: Path) -> str:
    stem = path.stem
    return stem.removeprefix("agent-")


def _agent_detail(path: Path, result: Any) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    agent_id = _agent_id_from_path(path)
    for row in _read_jsonl(path):
        row_agent_id = row.get("agentId")
        if isinstance(row_agent_id, str) and row_agent_id:
            agent_id = row_agent_id
        events.extend(_message_events(row))
    return {
        "agent_id": agent_id,
        "result": result,
        "events": events,
    }


def build_subagent_detail(run_dir: Path, task_id: str) -> dict[str, Any]:
    """Build a UI-friendly view of a Task/Workflow subagent transcript.

    The workflow event stream only contains lifecycle summaries. Claude's SDK
    writes the actual prompt/tool/text transcript under the transcript directory
    reported by the Task launch result in stdout.log.
    """

    refs = _task_refs(run_dir, task_id)
    transcript_dir_value = refs.get("transcript_dir")
    detail: dict[str, Any] = {
        "task_id": task_id,
        "transcript_dir": transcript_dir_value,
        "workflow_subrun_id": refs.get("workflow_subrun_id"),
        "task_output_status": refs.get("task_output_status"),
        "task_output": refs.get("task_output"),
        "agents": [],
    }
    if not transcript_dir_value:
        return detail
    transcript_dir = Path(str(transcript_dir_value)).expanduser()
    if not transcript_dir.is_dir():
        return detail

    journal_order, journal_results = _journal(transcript_dir)
    files = sorted(transcript_dir.glob("agent-*.jsonl"))
    by_agent_id: dict[str, dict[str, Any]] = {}
    for path in files:
        parsed = _agent_detail(path, None)
        parsed["result"] = journal_results.get(parsed["agent_id"], journal_results.get(_agent_id_from_path(path)))
        by_agent_id[parsed["agent_id"]] = parsed
    ordered_ids = [agent_id for agent_id in journal_order if agent_id in by_agent_id]
    ordered_ids.extend(agent_id for agent_id in by_agent_id if agent_id not in ordered_ids)
    detail["agents"] = [by_agent_id[agent_id] for agent_id in ordered_ids]
    return detail
