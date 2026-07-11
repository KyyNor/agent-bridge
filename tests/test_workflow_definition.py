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


def test_summary_constraints_apply_when_type_originates_as_string():
    graph = WorkflowGraph()
    try:
        validate_graph(graph, WorkflowType("summary"))
    except WorkflowDefinitionValidationError as exc:
        assert any("Markdown 和 HTML" in issue.message for issue in exc.issues)
    else:
        raise AssertionError("expected summary validation error")


def test_get_task_must_be_the_only_root_node():
    graph = WorkflowGraph.model_validate({"nodes": [
        {"id": "task", "type": "get_task", "name": "Task", "position": {"x": 0, "y": 0}},
        {"id": "other", "type": "agent", "name": "Other", "position": {"x": 1, "y": 0}, "config": {"prompt": "x", "backend_key": "claude"}},
    ]})
    try:
        validate_graph(graph, WorkflowType.operation)
    except WorkflowDefinitionValidationError as exc:
        assert any(issue.message == "获取任务节点必须是工作流唯一根节点" for issue in exc.issues)
    else:
        raise AssertionError("expected unique-root validation error")


def test_edge_condition_cannot_read_parallel_node_output():
    graph = WorkflowGraph.model_validate({"nodes": [
        {"id": item, "type": "agent", "name": item, "position": {"x": 0, "y": 0}, "config": {"prompt": item, "backend_key": "claude"}}
        for item in ("start", "left", "right", "end")
    ], "edges": [
        {"id": "start-left", "source": "start", "target": "left"},
        {"id": "start-right", "source": "start", "target": "right"},
        {"id": "left-end", "source": "left", "target": "end", "condition": {"field": "nodes.right.output.kind", "operator": "equals", "value": "ok"}},
    ]})
    try:
        validate_graph(graph, WorkflowType.operation)
    except WorkflowDefinitionValidationError as exc:
        assert any(issue.id == "left-end" and issue.field == "condition.field" for issue in exc.issues)
    else:
        raise AssertionError("expected condition dependency validation error")
