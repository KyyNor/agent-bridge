"""Unit tests for the pure diff/syntax primitives in core/diff.py and scripts/syntax.py."""
from __future__ import annotations

from agent_bridge.core.diff import text_diff, workflow_structured_diff
from agent_bridge.system_config.scripts.syntax import check_python_syntax


# --- text_diff ---------------------------------------------------------------


def test_text_diff_marks_identical_content():
    result = text_diff("a\nb\n", "a\nb\n", from_label="x", to_label="y")
    assert result["identical"] is True
    assert result["content"] == ""


def test_text_diff_emits_unified_hunks():
    result = text_diff("a\nb\nc\n", "a\nB\nc\n", from_label="v1", to_label="v2")
    assert result["identical"] is False
    assert "@@" in result["content"]
    assert "-b" in result["content"]
    assert "+B" in result["content"]


def test_workflow_diff_ignores_node_position_and_preserves_empty_container_changes():
    before = _wf([
        {"id": "n1", "type": "agent", "name": "N1", "position": {"x": 0, "y": 0}, "config": {}},
    ])
    moved = _wf([
        {"id": "n1", "type": "agent", "name": "N1", "position": {"x": 100, "y": 100}, "config": {}},
    ])
    assert workflow_structured_diff(before, moved)["identical"] is True

    changed = _wf([
        {"id": "n1", "type": "agent", "name": "N1", "position": {"x": 0, "y": 0}, "config": []},
    ])
    changes = workflow_structured_diff(before, changed)["nodes"]["changed"][0]["changes"]
    assert any(item["field"] == "config" and item["from"] == {} and item["to"] == [] for item in changes)


def test_text_diff_keeps_code_lines_that_start_with_unified_headers():
    result = text_diff("value\n", "+++ real code\n--- real code\n", from_label="v1", to_label="v2")
    assert "+ +++ real code" not in result["content"]
    assert "+++ real code" in result["content"]


# --- workflow_structured_diff -----------------------------------------------


def _wf(nodes, name="A"):
    return {
        "name": name, "description": "d", "status": "active",
        "workflow_type": "operation", "profile_key": "p1",
        "definition": {"nodes": nodes, "edges": []},
    }


def test_workflow_diff_reports_identical_when_unchanged():
    before = _wf([{"id": "n1", "type": "get_task", "name": "N1"}])
    assert workflow_structured_diff(before, dict(before))["identical"] is True


def test_workflow_diff_detects_added_removed_changed_nodes():
    before = _wf([{"id": "n1", "type": "get_task", "name": "N1"}])
    after = _wf(
        [
            {"id": "n1", "type": "get_task", "name": "N1-new"},  # changed
            {"id": "n2", "type": "output", "name": "N2"},        # added
        ],
        name="A2",
    )
    # remove n-original from before-equivalent: simulate a removed node
    before2 = _wf([
        {"id": "n1", "type": "get_task", "name": "N1"},
        {"id": "n9", "type": "agent", "name": "Gone"},
    ])
    diff = workflow_structured_diff(before2, after)
    assert diff["identical"] is False
    assert [n["id"] for n in diff["nodes"]["removed"]] == ["n9"]
    assert [n["id"] for n in diff["nodes"]["added"]] == ["n2"]
    assert [n["id"] for n in diff["nodes"]["changed"]] == ["n1"]
    assert any(m["field"] == "name" and m["from"] == "A" and m["to"] == "A2" for m in diff["metadata"])


# --- check_python_syntax ----------------------------------------------------


def test_syntax_ok_for_valid_code():
    assert check_python_syntax("x = 1\n")["ok"] is True
    assert check_python_syntax("def f():\n    return 1\n")["ok"] is True


def test_syntax_reports_line_and_column_for_errors():
    result = check_python_syntax("def f(\n")
    assert result["ok"] is False
    err = result["errors"][0]
    assert err["line"] == 1
    assert err["col"] is not None
    assert err["msg"]


def test_syntax_handles_empty_and_non_string():
    assert check_python_syntax("")["ok"] is True
    bad = check_python_syntax(123)  # type: ignore[arg-type]
    assert bad["ok"] is False
