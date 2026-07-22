from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, replace
from typing import Any, Literal

from agent_bridge.automation.workflows.definition import WorkflowGraph, WorkflowNode
from agent_bridge.automation.workflows.handlers import NodeExecutionContext, NodeExecutionResult
from agent_bridge.automation.workflows.incremental import IncrementalPlan, NodePlan
from agent_bridge.automation.workflows.models import WorkflowType
from agent_bridge.automation.workflows.references import evaluate_condition
from agent_bridge.automation.workflows.validation import (
    WorkflowDefinitionValidationError,
    collect_graph_issues,
)
from agent_bridge.core.timeutil import parse_utc, utc_now

TERMINAL_NODE_STATUSES = {"completed", "skipped", "failed", "cancelled", "warning"}


@dataclass(frozen=True)
class WorkflowExecutionResult:
    status: Literal["completed", "no_task", "failed"]
    output: dict[str, Any]
    task: dict[str, Any] | None
    error: str | None
    warnings: list[str]
    node_statuses: dict[str, str]


class WorkflowDagExecutor:
    def __init__(
        self,
        *,
        store: Any,
        handlers: Any,
        validate_structure_on_run: bool = True,
    ) -> None:
        self.store = store
        self.handlers = handlers
        self._validate_structure_on_run = validate_structure_on_run

    async def run(
        self,
        *,
        workflow: dict[str, Any],
        run_id: str,
        input_data: dict[str, Any],
        actor: str,
        plan: IncrementalPlan | None = None,
    ) -> WorkflowExecutionResult:
        graph = workflow["definition"]
        if isinstance(graph, dict):
            graph = WorkflowGraph.model_validate(graph)
        workflow_type = WorkflowType(workflow.get("workflow_type", WorkflowType.operation.value))
        if self._validate_structure_on_run:
            issues = collect_graph_issues(graph, workflow_type)
            if issues:
                raise WorkflowDefinitionValidationError(issues)

        nodes = {node.id: node for node in graph.nodes}
        incoming = {node.id: [] for node in graph.nodes}
        for edge in graph.edges:
            incoming[edge.target].append(edge)
        statuses = {node.id: "pending" for node in graph.nodes}
        outputs: dict[str, dict[str, Any]] = {}
        task: dict[str, Any] | None = None
        warnings: list[str] = []
        node_plans = self._node_plans(nodes, plan)
        reused_sources: dict[str, dict[str, Any]] = {}
        self.store.create_workflow_node_runs(
            run_id,
            [{"node_id": node.id, "node_type": node.type} for node in graph.nodes],
        )
        pending = set(nodes)
        running: dict[asyncio.Task, tuple[WorkflowNode, list[dict[str, Any]], NodePlan]] = {}

        while pending or running:
            ready: list[tuple[WorkflowNode, list[dict[str, Any]]]] = []
            skipped: list[tuple[WorkflowNode, list[dict[str, Any]]]] = []
            for node_id in sorted(pending):
                edges = incoming[node_id]
                if not all(statuses[edge.source] in TERMINAL_NODE_STATUSES for edge in edges):
                    continue
                condition_results = self._condition_results(edges, input_data, task, outputs)
                active = any(
                    item["matched"] and statuses[edge.source] in {"completed", "warning"}
                    for item, edge in zip(condition_results, edges)
                )
                if edges and not active:
                    skipped.append((nodes[node_id], condition_results))
                else:
                    ready.append((nodes[node_id], condition_results))

            for node, condition_results in skipped:
                pending.remove(node.id)
                statuses[node.id] = "skipped"
                node_plan = node_plans[node.id]
                self.store.finish_workflow_node_run(
                    run_id,
                    node.id,
                    status="skipped",
                    condition_results=condition_results,
                    node_fingerprint=node_plan.node_fingerprint,
                    action="execute",
                    reuse_reason="condition_not_matched",
                )
            if skipped:
                continue

            reused_progress = False
            for node, condition_results in ready:
                pending.remove(node.id)
                statuses[node.id] = "running"
                self.store.start_workflow_node_run(run_id, node.id, condition_results)
                node_plan = self._validated_node_plan(node_plans[node.id])
                node_plans[node.id] = node_plan
                if node_plan.action == "reuse":
                    result = NodeExecutionResult(
                        output=dict(node_plan.output_json or {}),
                        artifact_ids=list(node_plan.artifact_ids),
                    )
                    payload = self._persist_result(
                        run_id, node, condition_results, result, statuses, outputs, node_plan
                    )
                    reused_sources[node.id] = self._source_payload(node_plan)
                    reused_progress = True
                    if node.type == "get_task":
                        task = payload.get("task")
                        if task is None and node.config.on_empty == "terminate":
                            await self._cancel_running(run_id, running, statuses)
                            for node_id in sorted(pending):
                                statuses[node_id] = "skipped"
                                pending_plan = node_plans[node_id]
                                self.store.finish_workflow_node_run(
                                    run_id,
                                    node_id,
                                    status="skipped",
                                    node_fingerprint=pending_plan.node_fingerprint,
                                    action="execute",
                                    reuse_reason="no_task",
                                )
                            return WorkflowExecutionResult("no_task", {}, None, None, warnings, statuses)
                    continue
                context = NodeExecutionContext(
                    actor=actor,
                    workflow=workflow,
                    run_id=run_id,
                    input=input_data,
                    task=task,
                    nodes=outputs,
                    graph=graph,
                    execution_mode=plan.mode if plan is not None else "normal",
                    reused_sources=dict(reused_sources) or None,
                    node_fingerprint=node_plan.node_fingerprint,
                )
                running[asyncio.create_task(self.handlers.execute(node, context))] = (
                    node,
                    condition_results,
                    node_plan,
                )

            if not running:
                if pending:
                    if reused_progress:
                        continue
                    error = "工作流调度停滞：没有可运行节点"
                    for node_id in sorted(pending):
                        statuses[node_id] = "failed"
                        self.store.finish_workflow_node_run(
                            run_id, node_id, status="failed", error=error
                        )
                    return WorkflowExecutionResult(
                        "failed",
                        self._execution_output(workflow_type, graph, statuses, outputs),
                        task,
                        error,
                        warnings,
                        statuses,
                    )
                break

            done, _ = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
            batch_error: str | None = None
            no_task = False
            for execution in done:
                node, condition_results, node_plan = running.pop(execution)
                try:
                    result = execution.result()
                except asyncio.CancelledError:
                    error = f"节点执行被取消: {node.id}"
                    statuses[node.id] = "cancelled"
                    self.store.finish_workflow_node_run(
                        run_id,
                        node.id,
                        status="cancelled",
                        condition_results=condition_results,
                        error=error,
                        node_fingerprint=node_plan.node_fingerprint,
                        action="execute",
                        reuse_reason=node_plan.reason,
                    )
                    batch_error = batch_error or error
                    continue
                except Exception as exc:
                    error = str(exc)
                    statuses[node.id] = "failed"
                    self.store.finish_workflow_node_run(
                        run_id,
                        node.id,
                        status="failed",
                        condition_results=condition_results,
                        error=error,
                        node_fingerprint=node_plan.node_fingerprint,
                        action="execute",
                        reuse_reason=node_plan.reason,
                    )
                    batch_error = batch_error or error
                    continue

                payload = self._persist_result(
                    run_id, node, condition_results, result, statuses, outputs, node_plan
                )
                if result.status == "warning" and result.error:
                    warnings.append(result.error)
                if node.type == "get_task":
                    task = payload.get("task")
                    no_task = task is None and node.config.on_empty == "terminate"

            if batch_error is not None:
                await self._cancel_running(run_id, running, statuses)
                return WorkflowExecutionResult(
                    "failed",
                    self._execution_output(workflow_type, graph, statuses, outputs),
                    task,
                    batch_error,
                    warnings,
                    statuses,
                )
            if no_task:
                await self._cancel_running(run_id, running, statuses)
                for node_id in sorted(pending):
                    statuses[node_id] = "skipped"
                    node_plan = node_plans[node_id]
                    self.store.finish_workflow_node_run(
                        run_id,
                        node_id,
                        status="skipped",
                        node_fingerprint=node_plan.node_fingerprint,
                        action="execute",
                        reuse_reason="no_task",
                    )
                return WorkflowExecutionResult("no_task", {}, None, None, warnings, statuses)

        return WorkflowExecutionResult(
            "completed",
            self._execution_output(workflow_type, graph, statuses, outputs),
            task,
            None,
            warnings,
            statuses,
        )

    def _persist_result(
        self,
        run_id: str,
        node: WorkflowNode,
        condition_results: list[dict[str, Any]],
        result: NodeExecutionResult,
        statuses: dict[str, str],
        outputs: dict[str, dict[str, Any]],
        node_plan: NodePlan,
    ) -> dict[str, Any]:
        statuses[node.id] = result.status
        payload = dict(result.output)
        if result.artifact_ids:
            payload.setdefault("artifact_ids", result.artifact_ids)
        outputs[node.id] = {
            "status": result.status,
            "output": payload,
            "type": node.type,
            "format": getattr(node.config, "format", None),
        }
        self.store.finish_workflow_node_run(
            run_id,
            node.id,
            status=result.status,
            condition_results=condition_results,
            output=payload,
            error=result.error,
            agent_run_key=result.agent_run_key,
            script_run_id=result.script_run_id,
            artifact_ids=list(result.artifact_ids),
            node_fingerprint=node_plan.node_fingerprint,
            action=node_plan.action,
            reuse_reason=node_plan.reason,
            source_run_id=node_plan.source_run_id,
            source_node_id=node_plan.source_node_id,
            source_node_fingerprint=node_plan.source_node_fingerprint,
        )
        if node_plan.action == "reuse" and result.artifact_ids:
            self.store.associate_workflow_run_artifacts(
                run_id,
                node.id,
                list(result.artifact_ids),
                source_run_id=node_plan.source_run_id,
                source_node_id=node_plan.source_node_id,
            )
        return payload

    def _node_plans(
        self, nodes: dict[str, WorkflowNode], plan: IncrementalPlan | None
    ) -> dict[str, NodePlan]:
        if plan is None:
            return {
                node_id: NodePlan(
                    node_id=node_id,
                    action="execute",
                    reason="normal_mode",
                    node_fingerprint="",
                )
                for node_id in nodes
            }
        supplied = {node_plan.node_id: node_plan for node_plan in plan.nodes}
        return {
            node_id: supplied.get(
                node_id,
                NodePlan(
                    node_id=node_id,
                    action="execute",
                    reason="plan_node_missing",
                    node_fingerprint="",
                ),
            )
            for node_id in nodes
        }

    def _validated_node_plan(self, node_plan: NodePlan) -> NodePlan:
        if node_plan.action != "reuse":
            return node_plan
        artifact_ids = set(node_plan.artifact_ids)
        output_artifact_ids = (node_plan.output_json or {}).get("artifact_ids")
        if not isinstance(output_artifact_ids, (list, tuple)):
            output_artifact_ids = []
        artifact_ids.update(str(artifact_id) for artifact_id in output_artifact_ids)
        node_plan = replace(node_plan, artifact_ids=tuple(sorted(artifact_ids)))
        reason = self._reuse_artifact_error(node_plan)
        if reason is None:
            return node_plan
        return replace(
            node_plan,
            action="execute",
            reason=reason,
            source_run_id=None,
            source_node_id=None,
            source_node_fingerprint=None,
            output_json=None,
            artifact_ids=(),
        )

    def _reuse_artifact_error(self, node_plan: NodePlan) -> str | None:
        if not node_plan.artifact_ids:
            return None
        get_artifact = getattr(self.store, "get_workflow_artifact", None)
        if get_artifact is None:
            return "source_artifact_validation_unavailable"
        for artifact_id in node_plan.artifact_ids:
            artifact = get_artifact(artifact_id)
            if artifact is None:
                return "source_artifact_missing"
            if artifact.get("reusable") is False or artifact.get("is_reusable") is False or artifact.get("reuse_allowed") is False:
                return "source_artifact_not_reusable"
            if artifact.get("invalid_reason"):
                return str(artifact["invalid_reason"])
            if artifact.get("content_hash") and hashlib.sha256(str(artifact.get("content") or "").encode("utf-8")).hexdigest() != str(artifact["content_hash"]):
                return "source_artifact_hash_mismatch"
            if artifact.get("status") in {"deleted", "expired", "invalid"}:
                return "source_artifact_not_reusable"
            expires_at = artifact.get("expires_at")
            if expires_at and self._expired(expires_at):
                return "source_artifact_expired"
        return None

    @staticmethod
    def _expired(value: Any) -> bool:
        parsed = parse_utc(value)
        return parsed is None or parsed <= utc_now()

    @staticmethod
    def _source_payload(node_plan: NodePlan) -> dict[str, Any]:
        return {
            "source_run_id": node_plan.source_run_id,
            "source_node_id": node_plan.source_node_id,
            "source_node_fingerprint": node_plan.source_node_fingerprint,
            "reason": node_plan.reason,
        }

    @staticmethod
    def _condition_results(edges, input_data, task, outputs) -> list[dict[str, Any]]:
        context = {"input": input_data, "task": task, "nodes": outputs}
        result: list[dict[str, Any]] = []
        for edge in edges:
            evaluated = evaluate_condition(edge.condition, context)
            result.append(
                {
                    "edge_id": edge.id,
                    "field": edge.condition.field if edge.condition else None,
                    "operator": edge.condition.operator if edge.condition else None,
                    "expected": edge.condition.value if edge.condition else None,
                    "actual": evaluated.actual,
                    "matched": evaluated.matched,
                }
            )
        return result

    async def _cancel_running(
        self,
        run_id: str,
        running: dict[asyncio.Task, tuple[WorkflowNode, list[dict[str, Any]], NodePlan]],
        statuses: dict[str, str],
    ) -> None:
        for execution in running:
            execution.cancel()
        for execution, (node, condition_results, node_plan) in list(running.items()):
            try:
                result = await execution
            except asyncio.CancelledError:
                statuses[node.id] = "cancelled"
                self.store.finish_workflow_node_run(
                    run_id,
                    node.id,
                    status="cancelled",
                    condition_results=condition_results,
                    node_fingerprint=node_plan.node_fingerprint,
                    action="execute",
                    reuse_reason=node_plan.reason,
                )
            except Exception as exc:
                statuses[node.id] = "failed"
                self.store.finish_workflow_node_run(
                    run_id,
                    node.id,
                    status="failed",
                    condition_results=condition_results,
                    error=str(exc),
                    node_fingerprint=node_plan.node_fingerprint,
                    action="execute",
                    reuse_reason=node_plan.reason,
                )
            else:
                statuses[node.id] = result.status
                self.store.finish_workflow_node_run(
                    run_id,
                    node.id,
                    status=result.status,
                    condition_results=condition_results,
                    output=result.output,
                    error=result.error,
                    agent_run_key=result.agent_run_key,
                    script_run_id=result.script_run_id,
                    artifact_ids=list(result.artifact_ids),
                    node_fingerprint=node_plan.node_fingerprint,
                    action="execute",
                    reuse_reason=node_plan.reason,
                )
        running.clear()

    @staticmethod
    def _execution_output(
        workflow_type: WorkflowType,
        graph: WorkflowGraph,
        statuses: dict[str, str],
        outputs: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        if workflow_type is WorkflowType.summary:
            output_ids = [node.id for node in graph.nodes if node.type == "output"]
            return {
                node_id: outputs[node_id]["output"]
                for node_id in output_ids
                if node_id in outputs and statuses[node_id] in {"completed", "warning"}
            }
        sources = {edge.source for edge in graph.edges}
        terminal_ids = [node.id for node in graph.nodes if node.id not in sources]
        return {
            node_id: outputs[node_id]["output"]
            for node_id in terminal_ids
            if node_id in outputs and statuses[node_id] in {"completed", "warning"}
        }
