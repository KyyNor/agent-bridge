from __future__ import annotations
import hashlib
import json
import sqlite3
import uuid
from datetime import datetime
from typing import Any

from agent_bridge.automation.workflows.models import WorkflowTaskStatus
from agent_bridge.core.json_util import json_loads as _json_loads
from agent_bridge.core.timeutil import parse_utc, utc_iso
from agent_bridge.storage.types import row_to_dict


from agent_bridge.core.timeutil import parse_utc, utc_iso, utc_now


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _parse_datetime(value: Any) -> datetime | None:
    return parse_utc(value)


def _artifact_id() -> str:
    return f"artifact_{uuid.uuid4().hex}"


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _row_payload(row: sqlite3.Row | None) -> dict[str, Any] | None:
    item = row_to_dict(row)
    if item is None:
        return None
    if "is_current" in item:
        item["is_current"] = bool(item["is_current"])
    if "reuse_allowed" in item:
        item["reuse_allowed"] = bool(item["reuse_allowed"])
    if "has_artifacts" in item:
        item["has_artifacts"] = bool(item["has_artifacts"])
    if "needs_refresh" in item:
        item["needs_refresh"] = bool(item["needs_refresh"])
    for source, target, default in [
        ("payload_json", "payload", {}),
        ("tags_json", "tags", []),
        ("metadata_json", "metadata", {}),
        ("definition_snapshot_json", "definition_snapshot", {"nodes": [], "edges": []}),
        ("input_json", "input", {}),
        ("output_json", "output", {}),
        ("execution_plan_json", "execution_plan", {}),
        ("condition_results_json", "condition_results", []),
        ("artifact_ids_json", "artifact_ids", []),
    ]:
        if source in item:
            item[target] = _json_loads(item[source], default)
    if "definition_json" in item:
        item["definition"] = _json_loads(item["definition_json"], None)
    return item


def _workflow_task_import_payload(row: sqlite3.Row | None) -> dict[str, Any] | None:
    item = row_to_dict(row)
    if item is None:
        return None
    item["tasks"] = _json_loads(item.get("tasks_json"), [])
    item["preview"] = _json_loads(item.get("preview_json"), {})
    return item


def _workflow_definition_import_payload(row: sqlite3.Row | None) -> dict[str, Any] | None:
    item = row_to_dict(row)
    if item is None:
        return None
    item["workflow"] = _json_loads(item.pop("workflow_json", None), {})
    return item


def _datetime_iso(value: datetime | str) -> str:
    return utc_iso(value) if isinstance(value, datetime) else str(value)


def _run_summary_from_prefixed_row(row: dict[str, Any], prefix: str) -> dict[str, Any] | None:
    run_id = row.get(f"{prefix}run_id")
    if run_id is None:
        return None
    return {
        "run_id": run_id,
        "workflow_key": row["workflow_key"],
        "profile_key": row.get(f"{prefix}profile_key"),
        "task_key": row.get(f"{prefix}task_key"),
        "status": row.get(f"{prefix}status"),
        "workflow_revision_no": row.get(f"{prefix}workflow_revision_no"),
        "workflow_content_hash": row.get(f"{prefix}workflow_content_hash"),
        "task_version": row.get(f"{prefix}task_version"),
        "execution_mode": row.get(f"{prefix}execution_mode"),
        "source_run_id": row.get(f"{prefix}source_run_id"),
        "exit_code": row.get(f"{prefix}exit_code"),
        "error": row.get(f"{prefix}error"),
        "started_at": row.get(f"{prefix}started_at"),
        "finished_at": row.get(f"{prefix}finished_at"),
        "duration_ms": row.get(f"{prefix}duration_ms"),
    }
