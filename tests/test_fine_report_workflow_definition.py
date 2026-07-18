from __future__ import annotations

import json
from pathlib import Path

from agent_bridge.automation.workflows.definition import WorkflowGraph
from agent_bridge.automation.workflows.models import WorkflowType
from agent_bridge.automation.workflows.validation import validate_graph


WORKFLOW_FILE = Path(__file__).parents[1] / "examples/workflows/fine-report-analysis/workflow.json"


def test_fine_report_workflow_uses_the_current_structured_dag_contract():
    envelope = json.loads(WORKFLOW_FILE.read_text(encoding="utf-8"))
    assert envelope["format"] == "agent-bridge.workflow"
    assert envelope["format_version"] == 1
    workflow = envelope["workflow"]
    graph = WorkflowGraph.model_validate(workflow["definition"])

    validate_graph(graph, WorkflowType.operation)

    nodes = {node.id: node for node in graph.nodes}
    assert set(nodes) == {
        "get-task-initial",
        "seed-tasks",
        "get-task-retry",
        "visit-stats",
        "content-analysis",
        "lineage-trace",
        "report-output",
    }
    assert nodes["get-task-initial"].config.on_empty == "continue"
    assert nodes["get-task-retry"].config.on_empty == "terminate"
    assert nodes["seed-tasks"].config.script_key == "seed_fine_report_tasks"
    assert nodes["visit-stats"].config.script_key == "query_visit_stats"
    assert nodes["content-analysis"].config.result_mode == "json"
    assert nodes["lineage-trace"].config.result_mode == "json"
    assert nodes["report-output"].type == "output"
