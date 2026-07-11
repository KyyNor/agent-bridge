from agent_bridge.automation.workflows.definition import WorkflowGraph, default_workflow_graph
from agent_bridge.automation.workflows.models import WorkflowType
from agent_bridge.automation.workflows.validation import WorkflowDefinitionValidationError, validate_graph


def test_summary_default_graph_contains_locked_output_pair():
    graph = default_workflow_graph(WorkflowType.summary, "codex")
    assert [node.type for node in graph.nodes] == ["output", "output"]
    assert [node.config.format for node in graph.nodes] == ["markdown", "html"]
    assert [(edge.source, edge.target) for edge in graph.edges] == [("markdown-output", "html-output")]


def test_validate_graph_rejects_cycle():
    graph = WorkflowGraph.model_validate({
        "nodes": [
            {"id": "a", "type": "agent", "name": "A", "position": {"x": 0, "y": 0}, "config": {"prompt": "a", "backend_key": "claude"}},
            {"id": "b", "type": "agent", "name": "B", "position": {"x": 1, "y": 0}, "config": {"prompt": "b", "backend_key": "claude"}},
        ], "edges": [{"id": "ab", "source": "a", "target": "b"}, {"id": "ba", "source": "b", "target": "a"}],
    })
    try:
        validate_graph(graph, WorkflowType.operation)
    except WorkflowDefinitionValidationError as exc:
        assert any(issue.message == "工作流不能包含环" for issue in exc.issues)
    else:
        raise AssertionError("expected graph validation error")
