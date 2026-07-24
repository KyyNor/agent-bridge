"""Deterministic, conservative planning for workflow node-result reuse."""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Iterable, Literal, Mapping, Sequence

from agent_bridge.automation.workflows.definition import (
    edge_execution_payload,
    execution_fingerprint,
    node_execution_payload,
)
from agent_bridge.core.timeutil import parse_utc, utc_now

ExecutionMode = Literal["normal", "incremental", "force_full"]
NodeAction = Literal["execute", "reuse"]


def _frozen_mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if value is None:
        return None
    return MappingProxyType(dict(value))


@dataclass(frozen=True)
class NodePlan:
    node_id: str
    action: NodeAction
    reason: str
    node_fingerprint: str
    source_run_id: str | None = None
    source_node_id: str | None = None
    source_node_fingerprint: str | None = None
    output_json: Mapping[str, Any] | None = None
    artifact_ids: tuple[str, ...] = ()
    condition_results: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class IncrementalPlan:
    workflow_key: str
    workflow_revision_no: int | None
    workflow_content_hash: str | None
    task_version: str
    mode: ExecutionMode
    baseline_run_id: str | None
    nodes: tuple[NodePlan, ...]
    affected_node_ids: tuple[str, ...]
    reusable_node_ids: tuple[str, ...]
    reasons: Mapping[str, str]
    warnings: tuple[str, ...]


def _as_mapping(value: Any) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return dict(parsed) if isinstance(parsed, Mapping) else None
    return None


def _as_list(value: Any) -> list[Any] | None:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, list) else None
    return None


def _run_sort_key(run: Mapping[str, Any]) -> tuple[str, int, str]:
    return (
        str(run.get("finished_at") or run.get("started_at") or run.get("created_at") or ""),
        int(run.get("id") or 0),
        str(run.get("run_id") or ""),
    )


def _parse_timestamp(value: Any) -> datetime | None:
    return parse_utc(value)


class WorkflowIncrementalPlanner:
    """Build a single-run, all-or-nothing-at-each-node reuse plan.

    The planner has no store dependency: the scheduler can pass one or more
    historic run rows and their associated node/artifact rows without exposing
    persistence details here.
    """

    def node_fingerprint(self, node: Mapping[str, Any], *, runtime_fingerprint: Any = None) -> str:
        resource, _ = self._resource_fingerprint_for_node(node, runtime_fingerprint)
        return execution_fingerprint(node_execution_payload(node, resource_fingerprint=resource))

    @staticmethod
    def edge_fingerprint(edge: Mapping[str, Any]) -> str:
        return execution_fingerprint(edge_execution_payload(edge))

    def build(
        self,
        *,
        workflow: Mapping[str, Any],
        current_revision: Mapping[str, Any] | None,
        task: Mapping[str, Any],
        mode: ExecutionMode,
        baseline_run: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None,
        baseline_node_runs: Iterable[Mapping[str, Any]] | Mapping[str, Iterable[Mapping[str, Any]]] | None,
        baseline_artifacts: Iterable[Mapping[str, Any]] | Mapping[str, Iterable[Mapping[str, Any]]] | None,
        runtime_fingerprint: Any,
    ) -> IncrementalPlan:
        if mode not in {"normal", "incremental", "force_full"}:
            raise ValueError(f"unsupported execution mode: {mode}")
        graph = self._graph(workflow)
        nodes = [node for node in graph.get("nodes", []) if isinstance(node, Mapping) and node.get("id")]
        edges = [edge for edge in graph.get("edges", []) if isinstance(edge, Mapping)]
        workflow_key = str(workflow.get("workflow_key") or workflow.get("key") or "")
        profile_key = str(workflow.get("profile_key") or "")
        task_key = str(task.get("task_key") or task.get("key") or "")
        task_version = str(task.get("task_version") or task.get("version") or "")
        revision = current_revision or {}
        fingerprints = {
            str(node["id"]): self.node_fingerprint(node, runtime_fingerprint=runtime_fingerprint)
            for node in nodes
        }
        resources = {
            str(node["id"]): self._resource_fingerprint_for_node(node, runtime_fingerprint)
            for node in nodes
        }

        selected = self.select_baseline(
            baseline_run=baseline_run,
            workflow_key=workflow_key,
            profile_key=profile_key,
            task_key=task_key,
            task_version=task_version,
        )
        selected_id = str(selected.get("run_id")) if selected else None
        selected_node_runs = self._rows_for_run(baseline_node_runs, selected_id)
        selected_artifacts = self._rows_for_run(baseline_artifacts, selected_id)

        if mode == "normal":
            return self._all_execute(
                workflow_key, revision, task_version, mode, nodes, fingerprints, "normal_mode", selected_id
            )
        if mode == "force_full":
            return self._all_execute(
                workflow_key, revision, task_version, mode, nodes, fingerprints, "force_full", selected_id
            )
        if selected is None:
            return self._all_execute(
                workflow_key, revision, task_version, mode, nodes, fingerprints, "no_usable_baseline", None
            )

        baseline_graph = self._graph(selected.get("definition_snapshot") or selected.get("definition") or {})
        affected_reasons = self._direct_change_reasons(
            nodes=nodes,
            edges=edges,
            baseline_graph=baseline_graph,
            baseline_node_runs=selected_node_runs,
            resource_info=resources,
        )
        affected = self._downstream_closure(set(affected_reasons), edges)
        parents = self._parents(edges)
        previous_nodes = {str(row.get("node_id")): row for row in selected_node_runs if row.get("node_id")}
        artifacts_by_id = self._artifacts_by_id(selected_artifacts)

        plans_by_id: dict[str, NodePlan] = {}
        pending_ids = {str(node["id"]) for node in nodes}
        while pending_ids:
            progress = False
            for node in nodes:
                node_id = str(node["id"])
                if node_id not in pending_ids:
                    continue
                if any(parent_id not in plans_by_id for parent_id in parents.get(node_id, set())):
                    continue

                node_fingerprint = fingerprints[node_id]
                direct_reason = affected_reasons.get(node_id)
                node_type = str(node.get("type") or "")
                source = previous_nodes.get(node_id)
                if node_type == "get_task":
                    # 每次刷新租约；选中任务的业务输入未变化时，仍保留下游复用。
                    plan = self._execute_plan(
                        node_id,
                        node_fingerprint,
                        direct_reason or "task_lease_must_refresh",
                    )
                    affected.add(node_id)
                    task_changed = (
                        direct_reason is not None
                        or source is None
                        or source.get("status") != "completed"
                        or self._task_input_changed(task, source)
                    )
                    if task_changed:
                        affected.update(self._downstream_closure({node_id}, edges))
                elif direct_reason or node_id in affected:
                    plan = self._execute_plan(
                        node_id,
                        node_fingerprint,
                        direct_reason or "upstream_execute",
                    )
                else:
                    plan = self._reuse_or_execute(
                        node_id=node_id,
                        node_fingerprint=node_fingerprint,
                        source_run_id=selected_id,
                        source=source,
                        artifacts_by_id=artifacts_by_id,
                        workflow_key=workflow_key,
                        profile_key=profile_key,
                        task_key=task_key,
                        task_version=task_version,
                    )
                plans_by_id[node_id] = plan
                pending_ids.remove(node_id)
                progress = True
                if plan.action == "execute" and node_type != "get_task":
                    affected.update(self._downstream_closure({node_id}, edges))

            if progress:
                continue

            # 图校验会拒绝环，但规划器脱离校验边界使用时仍保持总有结果。
            for node_id in sorted(pending_ids):
                plans_by_id[node_id] = self._execute_plan(
                    node_id,
                    fingerprints[node_id],
                    "dependency_unresolved",
                )
            pending_ids.clear()

        plans = [plans_by_id[str(node["id"])] for node in nodes]
        reasons = {plan.node_id: plan.reason for plan in plans}

        return IncrementalPlan(
            workflow_key=workflow_key,
            workflow_revision_no=self._revision_no(revision),
            workflow_content_hash=self._revision_hash(revision),
            task_version=task_version,
            mode=mode,
            baseline_run_id=selected_id,
            nodes=tuple(plans),
            affected_node_ids=tuple(node.node_id for node in plans if node.node_id in affected),
            reusable_node_ids=tuple(node.node_id for node in plans if node.action == "reuse"),
            reasons=MappingProxyType(reasons),
            warnings=(),
        )

    def select_baseline(
        self,
        *,
        baseline_run: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None,
        workflow_key: str,
        profile_key: str,
        task_key: str,
        task_version: str,
    ) -> dict[str, Any] | None:
        if isinstance(baseline_run, Mapping):
            candidates = [baseline_run]
        else:
            candidates = list(baseline_run or [])
        compatible = [
            dict(run)
            for run in candidates
            if isinstance(run, Mapping)
            and run.get("status") == "completed"
            and str(run.get("workflow_key") or "") == workflow_key
            and str(run.get("profile_key") or "") == profile_key
            and str(run.get("task_key") or "") == task_key
            and str(run.get("task_version") or "") == task_version
        ]
        return max(compatible, key=_run_sort_key) if compatible else None

    @staticmethod
    def _graph(value: Any) -> dict[str, Any]:
        if isinstance(value, Mapping) and isinstance(value.get("definition"), Mapping):
            value = value["definition"]
        return dict(value) if isinstance(value, Mapping) else {"nodes": [], "edges": []}

    @staticmethod
    def _revision_no(revision: Mapping[str, Any]) -> int | None:
        value = revision.get("revision_no")
        return int(value) if value is not None else None

    @staticmethod
    def _revision_hash(revision: Mapping[str, Any]) -> str | None:
        value = revision.get("content_hash") or revision.get("workflow_content_hash")
        return str(value) if value is not None else None

    @staticmethod
    def _rows_for_run(rows: Any, run_id: str | None) -> list[dict[str, Any]]:
        if not run_id or rows is None:
            return []
        if isinstance(rows, Mapping):
            nested = rows.get(run_id)
            if nested is not None:
                return [dict(row) for row in nested if isinstance(row, Mapping)]
            values = rows.values()
        else:
            values = rows
        return [dict(row) for row in values if isinstance(row, Mapping) and str(row.get("run_id") or run_id) == run_id]

    def _resource_fingerprint_for_node(self, node: Mapping[str, Any], runtime: Any) -> tuple[Any, bool]:
        node_id = str(node.get("id") or "")
        node_type = str(node.get("type") or "")
        config = node.get("config") if isinstance(node.get("config"), Mapping) else {}
        if node_type == "get_task":
            return {"resource": "workflow-task-input"}, True
        value: Any = None
        found = False
        if isinstance(runtime, Mapping):
            for key in (node_id, "nodes", "resources", node_type, "default"):
                candidate = runtime.get(key)
                if key in {"nodes", "resources"} and isinstance(candidate, Mapping):
                    candidate = candidate.get(node_id)
                if candidate is not None:
                    value, found = candidate, True
                    break
        elif runtime is not None:
            value, found = runtime, True
        if isinstance(value, Mapping):
            if value.get("stable") is False or value.get("available") is False:
                return None, False
            value = value.get("fingerprint", value.get("value", value))
        if not found or value is None:
            return None, False
        return value, True

    def _direct_change_reasons(
        self,
        *,
        nodes: list[Mapping[str, Any]],
        edges: list[Mapping[str, Any]],
        baseline_graph: Mapping[str, Any],
        baseline_node_runs: list[Mapping[str, Any]],
        resource_info: Mapping[str, tuple[Any, bool]],
    ) -> dict[str, str]:
        previous_nodes = {str(node.get("id")): node for node in baseline_graph.get("nodes", []) if isinstance(node, Mapping)}
        previous_runs = {str(row.get("node_id")): row for row in baseline_node_runs if row.get("node_id")}
        current_inputs = self._incoming_edge_signatures(edges)
        previous_edges = [edge for edge in baseline_graph.get("edges", []) if isinstance(edge, Mapping)]
        previous_inputs = self._incoming_edge_signatures(previous_edges)
        reasons: dict[str, str] = {}
        for node in nodes:
            node_id = str(node["id"])
            _, stable = resource_info[node_id]
            if not stable:
                reasons[node_id] = "resource_fingerprint_unavailable"
            elif node_id not in previous_nodes or node_id not in previous_runs:
                reasons[node_id] = "new_node"
            elif current_inputs.get(node_id, ()) != previous_inputs.get(node_id, ()):
                reasons[node_id] = "incoming_edges_changed"
            elif execution_fingerprint(node_execution_payload(node)) != execution_fingerprint(
                node_execution_payload(previous_nodes[node_id])
            ):
                # Compare structure separately from the resource-bearing node
                # fingerprint.  This gives a complete downstream closure even
                # when diagram node order is not topological.
                reasons[node_id] = "node_fingerprint_changed"
            else:
                previous_fingerprint = previous_runs[node_id].get("node_fingerprint")
                if not previous_fingerprint:
                    reasons[node_id] = "baseline_fingerprint_missing"
        return reasons

    def _incoming_edge_signatures(self, edges: Iterable[Mapping[str, Any]]) -> dict[str, tuple[str, ...]]:
        incoming: dict[str, list[str]] = {}
        for edge in edges:
            target = edge.get("target")
            if target is None:
                continue
            incoming.setdefault(str(target), []).append(self.edge_fingerprint(edge))
        return {target: tuple(sorted(fingerprints)) for target, fingerprints in incoming.items()}

    @staticmethod
    def _parents(edges: Iterable[Mapping[str, Any]]) -> dict[str, set[str]]:
        result: dict[str, set[str]] = {}
        for edge in edges:
            source, target = edge.get("source"), edge.get("target")
            if source is not None and target is not None:
                result.setdefault(str(target), set()).add(str(source))
        return result

    @staticmethod
    def _downstream_closure(starts: set[str], edges: Iterable[Mapping[str, Any]]) -> set[str]:
        children: dict[str, set[str]] = {}
        for edge in edges:
            source, target = edge.get("source"), edge.get("target")
            if source is not None and target is not None:
                children.setdefault(str(source), set()).add(str(target))
        affected = set(starts)
        pending = list(starts)
        while pending:
            for child in children.get(pending.pop(), ()):
                if child not in affected:
                    affected.add(child)
                    pending.append(child)
        return affected

    @staticmethod
    def _artifacts_by_id(rows: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
        result: dict[str, Mapping[str, Any]] = {}
        for row in rows:
            artifact_id = row.get("artifact_id") or row.get("id")
            if artifact_id is not None:
                result[str(artifact_id)] = row
        return result

    @staticmethod
    def _task_input_fingerprint(value: Any) -> str | None:
        if not isinstance(value, Mapping):
            return None
        payload = value.get("payload")
        if payload is None:
            raw_payload = value.get("payload_json")
            if isinstance(raw_payload, str):
                try:
                    payload = json.loads(raw_payload)
                except json.JSONDecodeError:
                    payload = raw_payload
        return execution_fingerprint(
            {
                "task_key": str(value.get("task_key") or ""),
                "task_version": str(value.get("task_version") or ""),
                "type": str(value.get("type") or ""),
                "payload": payload,
            }
        )

    @classmethod
    def _task_input_changed(cls, current_task: Mapping[str, Any], source: Mapping[str, Any]) -> bool:
        source_output = _as_mapping(source.get("output") if "output" in source else source.get("output_json"))
        historic_task = source_output.get("task") if source_output is not None else None
        return cls._task_input_fingerprint(current_task) != cls._task_input_fingerprint(historic_task)

    def _reuse_or_execute(
        self,
        *,
        node_id: str,
        node_fingerprint: str,
        source_run_id: str,
        source: Mapping[str, Any] | None,
        artifacts_by_id: Mapping[str, Mapping[str, Any]],
        workflow_key: str,
        profile_key: str,
        task_key: str,
        task_version: str,
    ) -> NodePlan:
        if source is None:
            return self._execute_plan(node_id, node_fingerprint, "baseline_node_missing")
        if source.get("status") != "completed":
            return self._execute_plan(node_id, node_fingerprint, "baseline_node_not_completed")
        if source.get("node_fingerprint") != node_fingerprint:
            return self._execute_plan(node_id, node_fingerprint, "node_fingerprint_changed")
        output = _as_mapping(source.get("output") if "output" in source else source.get("output_json"))
        if output is None:
            return self._execute_plan(node_id, node_fingerprint, "baseline_output_missing")
        raw_artifact_ids = _as_list(source.get("artifact_ids") if "artifact_ids" in source else source.get("artifact_ids_json"))
        if raw_artifact_ids is None:
            return self._execute_plan(node_id, node_fingerprint, "artifact_ids_invalid")
        artifact_ids = tuple(str(artifact_id) for artifact_id in raw_artifact_ids)
        for artifact_id in artifact_ids:
            artifact = artifacts_by_id.get(artifact_id)
            if artifact is None:
                return self._execute_plan(node_id, node_fingerprint, "artifact_missing")
            if artifact.get("workflow_key") != workflow_key or artifact.get("profile_key") != profile_key or artifact.get("task_key") != task_key or str(artifact.get("task_version") or "") != task_version:
                return self._execute_plan(node_id, node_fingerprint, "artifact_scope_mismatch")
            if artifact.get("reusable") is False or artifact.get("is_reusable") is False or artifact.get("reuse_allowed") is False or artifact.get("status") in {"deleted", "expired", "invalid"}:
                return self._execute_plan(node_id, node_fingerprint, "artifact_not_reusable")
            if artifact.get("invalid_reason"):
                return self._execute_plan(node_id, node_fingerprint, str(artifact["invalid_reason"]))
            if artifact.get("content_hash") and hashlib.sha256(str(artifact.get("content") or "").encode("utf-8")).hexdigest() != str(artifact["content_hash"]):
                return self._execute_plan(node_id, node_fingerprint, "artifact_hash_mismatch")
            expires_at = _parse_timestamp(artifact.get("expires_at"))
            if expires_at is not None and expires_at <= utc_now():
                return self._execute_plan(node_id, node_fingerprint, "artifact_expired")
        conditions = _as_list(source.get("condition_results") if "condition_results" in source else source.get("condition_results_json"))
        # A successful node without conditional edges has no condition record.
        # Treat the omitted legacy field as an empty result set; malformed
        # explicit values remain non-reusable.
        if conditions is None and not ("condition_results" in source or "condition_results_json" in source):
            conditions = []
        if conditions is None or not all(isinstance(item, Mapping) for item in conditions):
            return self._execute_plan(node_id, node_fingerprint, "condition_results_invalid")
        return NodePlan(
            node_id=node_id,
            action="reuse",
            reason="fingerprint_match",
            node_fingerprint=node_fingerprint,
            source_run_id=source_run_id,
            source_node_id=node_id,
            source_node_fingerprint=str(source.get("node_fingerprint")),
            output_json=_frozen_mapping(output),
            artifact_ids=artifact_ids,
            condition_results=tuple(_frozen_mapping(item) or MappingProxyType({}) for item in conditions),
        )

    @staticmethod
    def _execute_plan(node_id: str, node_fingerprint: str, reason: str) -> NodePlan:
        return NodePlan(node_id=node_id, action="execute", reason=reason, node_fingerprint=node_fingerprint)

    def _all_execute(
        self,
        workflow_key: str,
        revision: Mapping[str, Any],
        task_version: str,
        mode: ExecutionMode,
        nodes: list[Mapping[str, Any]],
        fingerprints: Mapping[str, str],
        reason: str,
        baseline_run_id: str | None,
    ) -> IncrementalPlan:
        plans = tuple(self._execute_plan(str(node["id"]), fingerprints[str(node["id"])], reason) for node in nodes)
        return IncrementalPlan(
            workflow_key=workflow_key,
            workflow_revision_no=self._revision_no(revision),
            workflow_content_hash=self._revision_hash(revision),
            task_version=task_version,
            mode=mode,
            baseline_run_id=baseline_run_id,
            nodes=plans,
            affected_node_ids=tuple(node.node_id for node in plans),
            reusable_node_ids=(),
            reasons=MappingProxyType({node.node_id: reason for node in plans}),
            warnings=(),
        )
