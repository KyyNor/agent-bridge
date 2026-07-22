import json
from pathlib import Path

from agent_bridge.automation.workflows.definition import WorkflowGraph
from agent_bridge.automation.workflows.models import WorkflowType
from agent_bridge.automation.workflows.validation import collect_graph_issues


ROOT = Path(__file__).resolve().parents[1]


def test_tracked_workflow_examples_use_current_import_envelope_and_valid_graph() -> None:
    examples = sorted((ROOT / "examples" / "workflows").glob("*/workflow.json"))
    assert examples

    for path in examples:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        assert envelope["format"] == "agent-bridge.workflow", path
        assert envelope["format_version"] == 1, path
        workflow = envelope["workflow"]
        graph = WorkflowGraph.model_validate(workflow["definition"])
        issues = collect_graph_issues(graph, WorkflowType(workflow["workflow_type"]))
        assert issues == [], (path, issues)
