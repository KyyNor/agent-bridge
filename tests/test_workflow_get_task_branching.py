from __future__ import annotations

import pytest

from agent_bridge.automation.workflows.definition import WorkflowGraph
from agent_bridge.automation.workflows.executor import WorkflowDagExecutor
from agent_bridge.automation.workflows.validation import validate_graph
from agent_bridge.automation.workflows.models import WorkflowType
from agent_bridge.automation.workflows.handlers import NodeExecutionResult


def _branching_graph() -> dict:
    return {
        "nodes": [
            {
                "id": "get-task-initial",
                "type": "get_task",
                "name": "首次获取任务",
                "position": {"x": 0, "y": 0},
                "config": {"on_empty": "continue"},
            },
            {
                "id": "seed-tasks",
                "type": "script",
                "name": "灌入候选任务",
                "position": {"x": 240, "y": 120},
                "config": {"script_key": "seed-fine-report-tasks"},
            },
            {
                "id": "get-task-retry",
                "type": "get_task",
                "name": "重试获取任务",
                "position": {"x": 480, "y": 120},
                "config": {"on_empty": "terminate"},
            },
            {
                "id": "work",
                "type": "agent",
                "name": "处理任务",
                "position": {"x": 720, "y": 0},
                "config": {"prompt": "work", "backend_key": "claude"},
            },
        ],
        "edges": [
            {
                "id": "initial-to-seed",
                "source": "get-task-initial",
                "target": "seed-tasks",
                "condition": {
                    "field": "nodes.get-task-initial.output.task",
                    "operator": "equals",
                    "value": None,
                },
            },
            {
                "id": "initial-to-work",
                "source": "get-task-initial",
                "target": "work",
                "condition": {
                    "field": "nodes.get-task-initial.output.task",
                    "operator": "not_equals",
                    "value": None,
                },
            },
            {"id": "seed-to-retry", "source": "seed-tasks", "target": "get-task-retry"},
            {
                "id": "retry-to-work",
                "source": "get-task-retry",
                "target": "work",
                "condition": {
                    "field": "nodes.get-task-retry.output.task",
                    "operator": "not_equals",
                    "value": None,
                },
            },
        ],
    }


def test_validator_allows_one_root_get_task_and_one_retry_get_task():
    graph = WorkflowGraph.model_validate(_branching_graph())

    validate_graph(graph, WorkflowType.operation)


@pytest.mark.asyncio
async def test_empty_get_task_can_continue_to_seed_and_retry():
    graph = WorkflowGraph.model_validate(_branching_graph())

    class BranchHandlers:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def execute(self, node, context):
            self.calls.append(node.id)
            if node.id == "get-task-initial":
                return NodeExecutionResult(output={"task": None})
            if node.id == "seed-tasks":
                return NodeExecutionResult(output={"created": 1})
            if node.id == "get-task-retry":
                return NodeExecutionResult(
                    output={
                        "task": {
                            "task_key": "report-1",
                            "task_version": "v1",
                            "payload": {"report_id": "R001"},
                        }
                    }
                )
            return NodeExecutionResult(output={"processed": True})

    class Store:
        def create_workflow_node_runs(self, run_id, nodes):
            pass

        def start_workflow_node_run(self, run_id, node_id, condition_results):
            pass

        def finish_workflow_node_run(self, *args, **kwargs):
            pass

    handlers = BranchHandlers()
    result = await WorkflowDagExecutor(
        store=Store(),
        handlers=handlers,
        validate_structure_on_run=False,
    ).run(
        workflow={
            "workflow_key": "fine-report-analysis",
            "profile_key": "report-plane",
            "workflow_type": "operation",
            "definition": graph,
        },
        run_id="run-1",
        input_data={},
        actor="root",
    )

    assert result.status == "completed"
    assert result.task == {
        "task_key": "report-1",
        "task_version": "v1",
        "payload": {"report_id": "R001"},
    }
    assert handlers.calls == ["get-task-initial", "seed-tasks", "get-task-retry", "work"]


@pytest.mark.asyncio
async def test_existing_task_skips_seed_and_retry_branch():
    graph = WorkflowGraph.model_validate(_branching_graph())

    class ExistingTaskHandlers:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def execute(self, node, context):
            self.calls.append(node.id)
            if node.id == "get-task-initial":
                return NodeExecutionResult(
                    output={
                        "task": {
                            "task_key": "report-2",
                            "task_version": "v1",
                            "payload": {"report_id": "R002"},
                        }
                    }
                )
            return NodeExecutionResult(output={"processed": True})

    class Store:
        def create_workflow_node_runs(self, run_id, nodes):
            pass

        def start_workflow_node_run(self, run_id, node_id, condition_results):
            pass

        def finish_workflow_node_run(self, *args, **kwargs):
            pass

    handlers = ExistingTaskHandlers()
    result = await WorkflowDagExecutor(
        store=Store(),
        handlers=handlers,
        validate_structure_on_run=False,
    ).run(
        workflow={
            "workflow_key": "fine-report-analysis",
            "profile_key": "report-plane",
            "workflow_type": "operation",
            "definition": graph,
        },
        run_id="run-2",
        input_data={},
        actor="root",
    )

    assert result.status == "completed"
    assert result.task["task_key"] == "report-2"
    assert handlers.calls == ["get-task-initial", "work"]
