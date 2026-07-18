"""Diff helpers for entity version comparison.

Two pure-function diff primitives:

* :func:`text_diff` — line-oriented unified diff over raw text (scripts, skill
  prompts). Backed by the standard-library :mod:`difflib`.

* :func:`workflow_structured_diff` — semantic diff over a workflow definition
  snapshot. Reports added/removed/changed nodes, added/removed edges, and
  top-level metadata changes (name, description, status, type, profile).

The frontend re-renders diffs from snapshots for richer interaction; these
functions provide a server-side canonical computation and a unified-text
fallback. No third-party dependencies.
"""

from __future__ import annotations

import difflib
from typing import Any

_METADATA_FIELDS: tuple[str, ...] = (
    "name",
    "description",
    "status",
    "workflow_type",
    "profile_key",
)


class _Missing:
    """Sentinel for absent fields (distinct from ``None`` values)."""


_MISSING = _Missing()


def text_diff(
    from_text: str,
    to_text: str,
    *,
    from_label: str = "from",
    to_label: str = "to",
) -> dict[str, Any]:
    """Return a unified-diff envelope for two text blobs."""
    from_lines = (from_text or "").splitlines(keepends=True)
    to_lines = (to_text or "").splitlines(keepends=True)
    diff_iter = difflib.unified_diff(
        from_lines,
        to_lines,
        fromfile=from_label,
        tofile=to_label,
        lineterm="",
    )
    content = "\n".join(line.rstrip("\n") for line in diff_iter)
    return {
        "format": "unified",
        "content": content,
        "from_label": from_label,
        "to_label": to_label,
        "identical": not content,
    }


def _index_by_id(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for item in items or []:
        key = str(item.get("id") or "")
        if key:
            indexed[key] = item
    return indexed


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten a nested dict/list into a ``path -> value`` map for comparison."""
    flat: dict[str, Any] = {}
    if isinstance(value, dict):
        for k, v in value.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            flat.update(_flatten(v, path))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            path = f"{prefix}[{i}]"
            flat.update(_flatten(v, path))
    else:
        flat[prefix] = value
    return flat


def _diff_fields(
    before: dict[str, Any] | None, after: dict[str, Any] | None
) -> list[dict[str, Any]]:
    before = before or {}
    after = after or {}
    before_flat = _flatten(before)
    after_flat = _flatten(after)
    changes: list[dict[str, Any]] = []
    for key in sorted(set(before_flat) | set(after_flat)):
        old = before_flat.get(key, _MISSING)
        new = after_flat.get(key, _MISSING)
        if old is not _MISSING and new is _MISSING:
            changes.append({"field": key, "from": before_flat[key], "to": None})
        elif old is _MISSING and new is not _MISSING:
            changes.append({"field": key, "from": None, "to": after_flat[key]})
        elif old != new:
            changes.append({"field": key, "from": before_flat[key], "to": after_flat[key]})
    return changes


class _Missing:
    """Sentinel for absent fields (distinct from ``None`` values)."""


_MISSING = _Missing()


def workflow_structured_diff(
    from_snapshot: dict[str, Any] | None,
    to_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    """Semantic diff over two workflow definition snapshots.

    Each snapshot is the dict persisted to ``workflow_definition_revisions``;
    expected shape ``{"definition": {"nodes":[...], "edges":[...]}, "name": ...}``.
    """
    before = from_snapshot or {}
    after = to_snapshot or {}
    before_def = before.get("definition") or {}
    after_def = after.get("definition") or {}

    before_nodes = _index_by_id(before_def.get("nodes") or [])
    after_nodes = _index_by_id(after_def.get("nodes") or [])
    before_edges = _index_by_id(before_def.get("edges") or [])
    after_edges = _index_by_id(after_def.get("edges") or [])

    added_nodes = [
        {"id": nid, **_node_summary(after_nodes[nid])}
        for nid in after_nodes
        if nid not in before_nodes
    ]
    removed_nodes = [
        {"id": nid, **_node_summary(before_nodes[nid])}
        for nid in before_nodes
        if nid not in after_nodes
    ]
    changed_nodes = [
        {
            "id": nid,
            "changes": _diff_fields(before_nodes[nid], after_nodes[nid]),
        }
        for nid in before_nodes
        if nid in after_nodes and before_nodes[nid] != after_nodes[nid]
    ]

    added_edges = [
        {"id": eid, **_edge_summary(after_edges[eid])}
        for eid in after_edges
        if eid not in before_edges
    ]
    removed_edges = [
        {"id": eid, **_edge_summary(before_edges[eid])}
        for eid in before_edges
        if eid not in after_edges
    ]
    changed_edges = [
        {
            "id": eid,
            "changes": _diff_fields(before_edges[eid], after_edges[eid]),
        }
        for eid in before_edges
        if eid in after_edges and before_edges[eid] != after_edges[eid]
    ]

    metadata_changes: list[dict[str, Any]] = []
    for field in _METADATA_FIELDS:
        old = before.get(field)
        new = after.get(field)
        if old != new:
            metadata_changes.append({"field": field, "from": old, "to": new})

    return {
        "nodes": {"added": added_nodes, "removed": removed_nodes, "changed": changed_nodes},
        "edges": {"added": added_edges, "removed": removed_edges, "changed": changed_edges},
        "metadata": metadata_changes,
        "identical": not (
            added_nodes
            or removed_nodes
            or changed_nodes
            or added_edges
            or removed_edges
            or changed_edges
            or metadata_changes
        ),
    }


def _node_summary(node: dict[str, Any]) -> dict[str, Any]:
    return {"type": node.get("type"), "label": node.get("label") or node.get("data", {}).get("label")}


def _edge_summary(edge: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": edge.get("source"),
        "target": edge.get("target"),
        "source_handle": edge.get("source_handle"),
        "target_handle": edge.get("target_handle"),
    }
