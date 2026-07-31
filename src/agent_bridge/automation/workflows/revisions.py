"""工作流定义版本的公共载荷、快照和语义哈希。"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _semantic_node_payload(node: dict[str, Any]) -> dict[str, Any]:
    """Remove presentation and run-control fields from a node revision hash."""
    payload = {
        key: value
        for key, value in node.items()
        if key not in {"position", "name"}
    }
    if payload.get("type") in {"agent", "output"}:
        config = dict(payload.get("config") or {})
        # Agent 等待时间不改变节点的处理语义，不能产生仅用于增量的版本。
        config.pop("timeout_seconds", None)
        if payload.get("type") == "output":
            # 输出标题不参与 Agent 提示词或产物保存，属于编辑器展示信息。
            config.pop("title", None)
        payload["config"] = config
    return payload


def _full_node_payload(node: dict[str, Any]) -> dict[str, Any]:
    """版本口径的节点载荷：仅剥离 position，保留 name/timeout_seconds/title。

    与 :func:`_semantic_node_payload` 的差异在于保留展示与运行控制字段——这些
    字段变更需要产生新版本号（供版本历史与 diff 记录），但不应触发重跑（重跑
    判定使用 :func:`workflow_content_hash` 的执行语义口径）。
    """
    return {
        key: value
        for key, value in node.items()
        if key != "position"
    }


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
    # 工作流名称和描述是管理页面的展示信息，不改变节点处理或任务产物。
    execution_definition = {
        "nodes": [
            _semantic_node_payload(node)
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
            "profile_key": profile_key,
            "status": status,
            "workflow_type": workflow_type,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()


def workflow_version_hash(
    graph_payload: dict[str, Any],
    name: str,
    description: str,
    profile_key: str,
    status: str,
    workflow_type: str,
) -> str:
    """版本口径指纹：含 name/description，节点保留展示与运行控制字段。

    用于判定是否产生新 revision_no 与 diff。重跑/stale 判定不使用此指纹，
    那由 :func:`workflow_content_hash`（执行语义口径）负责。
    """
    version_definition = {
        "nodes": [
            _full_node_payload(node)
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
            "definition": version_definition,
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
