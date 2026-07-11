"""Integration coverage retained for structured output artifacts."""

from agent_bridge.automation.workflows.definition import WorkflowGraph


def test_summary_definition_keeps_the_required_markdown_html_pair():
    graph = WorkflowGraph.model_validate({
        "nodes": [
            {"id": "markdown-output", "type": "output", "name": "Markdown", "position": {"x": 0, "y": 0}, "config": {"format": "markdown", "title": "M", "path": "out.md", "prompt": "p", "backend_key": "codex"}},
            {"id": "html-output", "type": "output", "name": "HTML", "position": {"x": 1, "y": 0}, "config": {"format": "html", "title": "H", "path": "out.html", "prompt": "p", "backend_key": "codex"}},
        ],
        "edges": [{"id": "markdown-to-html", "source": "markdown-output", "target": "html-output"}],
    })
    assert [node.config.format for node in graph.nodes] == ["markdown", "html"]
