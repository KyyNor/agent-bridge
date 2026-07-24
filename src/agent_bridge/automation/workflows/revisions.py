"""工作流定义版本的公共载荷、快照和语义哈希。"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def definition_payload(workflow: dict[str, Any]) -> dict[str, Any]:
    payload = dict(workflow)
    payload.pop("definition_json", None)
    if "revision_no" not in payload:
        payload["revision_no"] = int(payload.get("current_revision_no") or 0)
    payload.pop("current_revision_no", None)
    return payload


def workflow_content_hash(
    graph_payload: dict[str, Any],
    name: str,
    description: str,
    profile_key: str,
    status: str,
    workflow_type: str,
) -> str:
    execution_definition = {
        "nodes": [
            {key: value for key, value in node.items() if key != "position"}
            for node in sorted(
                graph_payload.get("nodes") or [],
                key=lambda item: str(item.get("id") or ""),
            )
        ],
        "edges": sorted(
            graph_payload.get("edges") or [],
            key=lambda item: str(item.get("id") or ""),
        ),
    }
    fingerprint = json.dumps(
        {
            "definition": execution_definition,
            "name": name,
            "description": description,
            "profile_key": profile_key,
            "status": status,
            "workflow_type": workflow_type,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()


def workflow_revision_snapshot(workflow: dict[str, Any]) -> dict[str, Any]:
    """捕获重建和比较工作流版本所需的字段。"""
    return {
        "workflow_key": workflow.get("workflow_key"),
        "name": workflow.get("name"),
        "description": workflow.get("description"),
        "profile_key": workflow.get("profile_key"),
        "status": workflow.get("status"),
        "workflow_type": workflow.get("workflow_type"),
        "definition": workflow.get("definition"),
    }
