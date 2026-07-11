from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal

from agent_bridge.automation.workflows.definition import WorkflowGraph, WorkflowNode
from agent_bridge.automation.workflows.handlers import NodeExecutionContext, NodeExecutionError
from agent_bridge.automation.workflows.references import evaluate_condition

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
    def __init__(self, *, store: Any, handlers: Any) -> None:
        self.store = store
        self.handlers = handlers

    async def run(
        self, *, workflow: dict[str, Any], run_id: str, input_data: dict[str, Any], actor: str
    ) -> WorkflowExecutionResult:
        graph = workflow["definition"]
        if isinstance(graph, dict):
            graph = WorkflowGraph.model_validate(graph)
        nodes = {node.id: node for node in graph.nodes}
        incoming = {node.id: [] for node in graph.nodes}
        for edge in graph.edges:
            incoming[edge.target].append(edge)
        statuses = {node.id: "pending" for node in graph.nodes}
        outputs: dict[str, dict[str, Any]] = {}
        task: dict[str, Any] | None = None
        warnings: list[str] = []
        self.store.create_workflow_node_runs(run_id, [{"node_id": node.id, "node_type": node.type} for node in graph.nodes])
        pending = set(nodes)
        running: dict[asyncio.Task, tuple[WorkflowNode, list[dict[str, Any]]]] = {}

        while pending or running:
            ready: list[tuple[WorkflowNode, list[dict[str, Any]]]] = []
            for node_id in sorted(pending):
                edges = incoming[node_id]
                if not all(statuses[edge.source] in TERMINAL_NODE_STATUSES for edge in edges):
                    continue
                results = self._condition_results(edges, input_data, task, outputs)
                active = any(result["matched"] and statuses[edge.source] in {"completed", "warning"} for result, edge in zip(results, edges))
                if edges and not active:
                    statuses[node_id] = "skipped"
                    self.store.finish_workflow_node_run(run_id, node_id, status="skipped", condition_results=results)
                    pending.remove(node_id)
                    break
                ready.append((nodes[node_id], results))
            else:
                for node, results in ready:
                    pending.remove(node.id)
                    statuses[node.id] = "running"
                    self.store.start_workflow_node_run(run_id, node.id, results)
                    context = NodeExecutionContext(actor=actor, workflow=workflow, run_id=run_id, input=input_data, task=task, nodes=outputs)
                    running[asyncio.create_task(self.handlers.execute(node, context))] = (node, results)
                if not running:
                    continue
                done, _ = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
                for execution in done:
                    node, results = running.pop(execution)
                    try:
                        result = execution.result()
                    except NodeExecutionError as exc:
                        statuses[node.id] = "failed"
                        self.store.finish_workflow_node_run(run_id, node.id, status="failed", condition_results=results, error=str(exc))
                        await self._cancel_running(run_id, running)
                        return WorkflowExecutionResult("failed", self._terminal_output(graph, statuses, outputs), task, str(exc), warnings, statuses)
                    except Exception as exc:
                        statuses[node.id] = "failed"
                        self.store.finish_workflow_node_run(run_id, node.id, status="failed", condition_results=results, error=str(exc))
                        await self._cancel_running(run_id, running)
                        return WorkflowExecutionResult("failed", self._terminal_output(graph, statuses, outputs), task, str(exc), warnings, statuses)
                    statuses[node.id] = result.status
                    payload = dict(result.output)
                    if result.artifact_ids:
                        payload.setdefault("artifact_ids", result.artifact_ids)
                    outputs[node.id] = {"status": result.status, "output": payload, "type": node.type, "format": getattr(node.config, "format", None)}
                    if node.type == "get_task":
                        task = payload.get("task")
                        if task is None:
                            self.store.finish_workflow_node_run(run_id, node.id, status="completed", condition_results=results, output=payload)
                            return WorkflowExecutionResult("no_task", {}, None, None, warnings, statuses)
                    self.store.finish_workflow_node_run(
                        run_id, node.id, status=result.status, condition_results=results, output=payload,
                        error=result.error, agent_run_key=result.agent_run_key, script_run_id=result.script_run_id,
                    )
                    if result.status == "warning" and result.error:
                        warnings.append(result.error)
                continue
            # A skipped node was found; reevaluate dependencies before scheduling.
            continue
        return WorkflowExecutionResult("completed", self._terminal_output(graph, statuses, outputs), task, None, warnings, statuses)

    @staticmethod
    def _condition_results(edges, input_data, task, outputs) -> list[dict[str, Any]]:
        context = {"input": input_data, "task": task, "nodes": outputs}
        result: list[dict[str, Any]] = []
        for edge in edges:
            evaluated = evaluate_condition(edge.condition, context)
            result.append({
                "edge_id": edge.id,
                "field": edge.condition.field if edge.condition else None,
                "operator": edge.condition.operator if edge.condition else None,
                "expected": edge.condition.value if edge.condition else None,
                "actual": evaluated.actual,
                "matched": evaluated.matched,
            })
        return result

    async def _cancel_running(self, run_id: str, running: dict[asyncio.Task, tuple[WorkflowNode, list[dict[str, Any]]]]) -> None:
        for task in running:
            task.cancel()
        for task, (node, results) in running.items():
            try:
                await task
            except asyncio.CancelledError:
                self.store.finish_workflow_node_run(run_id, node.id, status="cancelled", condition_results=results)
            except Exception as exc:
                self.store.finish_workflow_node_run(run_id, node.id, status="failed", condition_results=results, error=str(exc))

    @staticmethod
    def _terminal_output(graph: WorkflowGraph, statuses, outputs) -> dict[str, Any]:
        sources = {edge.source for edge in graph.edges}
        terminal_ids = [node.id for node in graph.nodes if node.id not in sources]
        return {node_id: outputs[node_id]["output"] for node_id in terminal_ids if statuses[node_id] in {"completed", "warning"} and node_id in outputs}
