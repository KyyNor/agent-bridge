from agent_bridge.automation.workflows.definition import WorkflowGraph, default_workflow_graph, normalize_summary_graph
from agent_bridge.automation.workflows.models import WorkflowType
from agent_bridge.automation.workflows.validation import WorkflowDefinitionValidationError, validate_graph
from agent_bridge.core.domain import ValidationError


def test_summary_default_graph_contains_locked_output_pair():
    graph = default_workflow_graph(WorkflowType.summary, "codex")
    assert [node.type for node in graph.nodes] == ["output", "output"]
    assert [node.config.format for node in graph.nodes] == ["markdown", "html"]
    assert [node.config.system_role for node in graph.nodes] == ["summary_markdown", "summary_html"]
    assert graph.edges[0].system_role == "summary_markdown_to_html"
    assert [(edge.source, edge.target) for edge in graph.edges] == [("markdown-output", "html-output")]


def test_normalize_summary_graph_converts_single_ordinary_markdown_output_and_adds_html():
    graph = WorkflowGraph.model_validate({
        "nodes": [
            {"id": "task", "type": "get_task", "name": "Task", "position": {"x": 0, "y": 0}},
            {"id": "analysis", "type": "agent", "name": "Analysis", "position": {"x": 160, "y": 0}, "config": {"prompt": "分析", "backend_key": "claude"}},
            {"id": "report", "type": "output", "name": "Report", "position": {"x": 320, "y": 0}, "config": {"format": "markdown", "title": "Report", "path": "report.md", "prompt": "总结", "backend_key": "claude"}},
        ],
        "edges": [
            {"id": "task-analysis", "source": "task", "target": "analysis"},
            {"id": "analysis-report", "source": "analysis", "target": "report"},
        ],
    })

    normalized = normalize_summary_graph(graph, "claude")

    assert [node.id for node in normalized.nodes[-2:]] == ["report", "html-output"]
    assert normalized.nodes[-2].config.system_role == "summary_markdown"
    assert normalized.nodes[-1].config.system_role == "summary_html"
    assert [(edge.source, edge.target) for edge in normalized.edges if edge.system_role == "summary_markdown_to_html"] == [("report", "html-output")]
    assert any(edge.source == "analysis" and edge.target == "report" for edge in normalized.edges)


def test_normalize_summary_graph_generates_both_system_outputs_when_missing():
    graph = WorkflowGraph.model_validate({
        "nodes": [{"id": "task", "type": "get_task", "name": "Task", "position": {"x": 0, "y": 0}}],
        "edges": [],
    })

    normalized = normalize_summary_graph(graph, "codex")

    assert [node.config.system_role for node in normalized.nodes[-2:]] == ["summary_markdown", "summary_html"]
    assert [(edge.source, edge.target, edge.system_role) for edge in normalized.edges] == [("task", "markdown-output", None), ("markdown-output", "html-output", "summary_markdown_to_html")]


def test_normalize_summary_graph_preserves_existing_system_pair():
    graph = default_workflow_graph(WorkflowType.summary, "claude")
    normalized = normalize_summary_graph(graph, "codex")

    assert [node.id for node in normalized.nodes] == ["markdown-output", "html-output"]
    assert [node.config.backend_key for node in normalized.nodes] == ["claude", "claude"]


def test_normalize_summary_graph_rejects_extra_ordinary_output_with_system_pair():
    payload = default_workflow_graph(WorkflowType.summary, "claude").model_dump(mode="json")
    payload["nodes"].insert(0, {"id": "extra", "type": "output", "name": "Extra", "position": {"x": 0, "y": 0}, "config": {"format": "markdown", "title": "Extra", "path": "extra.md", "prompt": "extra", "backend_key": "claude"}})

    try:
        normalize_summary_graph(WorkflowGraph.model_validate(payload), "claude")
    except ValidationError as exc:
        assert "普通输出节点" in str(exc)
    else:
        raise AssertionError("expected extra ordinary output rejection")


def test_normalize_summary_graph_avoids_generated_id_collisions():
    graph = WorkflowGraph.model_validate({
        "nodes": [{"id": "markdown-output", "type": "agent", "name": "User", "position": {"x": 0, "y": 0}, "config": {"prompt": "x", "backend_key": "claude"}}],
        "edges": [],
    })

    normalized = normalize_summary_graph(graph, "claude")

    assert len({node.id for node in normalized.nodes}) == len(normalized.nodes)
    assert [node.config.system_role for node in normalized.nodes[-2:]] == ["summary_markdown", "summary_html"]


def test_summary_validation_uses_markers_without_rejecting_ordinary_output_nodes():
    default = default_workflow_graph(WorkflowType.summary, "claude").model_dump(mode="json")
    default["nodes"].insert(
        0,
        {
            "id": "ordinary-output",
            "type": "output",
            "name": "Ordinary output",
            "position": {"x": 0, "y": 0},
            "config": {
                "format": "markdown",
                "title": "Ordinary",
                "path": "ordinary.md",
                "prompt": "ordinary",
                "backend_key": "claude",
            },
        },
    )
    default["edges"].insert(
        0,
        {
            "id": "ordinary-to-summary",
            "source": "ordinary-output",
            "target": "markdown-output",
        },
    )

    validate_graph(WorkflowGraph.model_validate(default), WorkflowType.summary)


def test_summary_output_pair_requires_one_unconditional_direct_edge():
    payload = default_workflow_graph(WorkflowType.summary, "codex").model_dump(mode="json")
    payload["edges"][0]["condition"] = {
        "field": "nodes.markdown-output.output.content",
        "operator": "exists",
    }
    graph = WorkflowGraph.model_validate(payload)

    try:
        validate_graph(graph, WorkflowType.summary)
    except WorkflowDefinitionValidationError as exc:
        assert any(issue.field == "condition" for issue in exc.issues)
    else:
        raise AssertionError("expected protected summary edge validation error")


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
