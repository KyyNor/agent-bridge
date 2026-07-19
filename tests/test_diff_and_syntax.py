"""Unit tests for the pure diff/syntax primitives in core/diff.py and scripts/syntax.py."""
from __future__ import annotations

from agent_bridge.core.diff import (
    _inline_diff,
    _maybe_inline,
    text_diff,
    workflow_structured_diff,
)
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


# --- inline (token-level) diff ----------------------------------------------


def test_inline_diff_marks_single_token_insertion():
    """The user's reported case: insert a single char in a long prompt."""
    before = "查询 metadata_fine_cpt_raw_content 获取原始 XML"
    after = "1查询 metadata_fine_cpt_raw_content 获取原始 XML"
    segments = _inline_diff(before, after)
    # Exactly one "add" segment carrying the inserted char, rest is context.
    adds = [s for s in segments if s["type"] == "add"]
    dels = [s for s in segments if s["type"] == "del"]
    assert dels == []
    assert len(adds) == 1
    assert adds[0]["text"] == "1"
    # Concatenating ctx + add reconstructs `after`.
    assert "".join(s["text"] for s in segments if s["type"] in ("ctx", "add")) == after
    # Concatenating ctx + del reconstructs `before`.
    assert "".join(s["text"] for s in segments if s["type"] in ("ctx", "del")) == before


def test_inline_diff_preserves_ascii_identifiers():
    """ASCII identifiers stay as one token — renaming is a clean add/del pair."""
    before = "使用 cpt_file_path 读取"
    after = "使用 report_id 读取"
    segments = _inline_diff(before, after)
    dels = "".join(s["text"] for s in segments if s["type"] == "del")
    adds = "".join(s["text"] for s in segments if s["type"] == "add")
    assert "cpt_file_path" in dels
    assert "report_id" in adds
    # The identifiers were not split character-by-character.
    assert "c" not in [s["text"] for s in segments if s["type"] == "del"]


def test_inline_diff_merges_adjacent_same_type_segments():
    """Consecutive del/add segments are merged into single runs."""
    segments = _inline_diff("abcdef", "abXYef")
    types = [s["type"] for s in segments]
    # No two adjacent equal types.
    for a, b in zip(types, types[1:]):
        assert a != b


def test_inline_diff_is_none_for_short_strings():
    assert _maybe_inline("abc", "abd") is None
    assert _maybe_inline("short", "shor") is None


def test_inline_diff_is_none_for_non_strings():
    assert _maybe_inline(1, 2) is None
    assert _maybe_inline(True, False) is None
    assert _maybe_inline({"a": 1}, {"a": 2}) is None
    assert _maybe_inline(None, "long enough string") is None


def test_workflow_diff_attaches_inline_on_long_prompt_change():
    """A long config.prompt change carries an `inline` token-level diff."""
    prompt_before = (
        "使用当前任务上下文中的 cpt_file_path 和 report_id，查询 "
        "metadata_fine_cpt_raw_content 获取原始 XML，解析报表结构。"
    )
    prompt_after = "1" + prompt_before  # single-char insertion
    before = _wf([{"id": "n1", "type": "agent", "config": {"prompt": prompt_before}}])
    after = _wf([{"id": "n1", "type": "agent", "config": {"prompt": prompt_after}}])
    changes = workflow_structured_diff(before, after)["nodes"]["changed"][0]["changes"]
    prompt_change = next(c for c in changes if c["field"] == "config.prompt")
    assert "inline" in prompt_change
    adds = [s["text"] for s in prompt_change["inline"] if s["type"] == "add"]
    assert adds == ["1"]


def test_workflow_diff_omits_inline_for_short_field_change():
    """Short string changes keep the legacy shape (no `inline` key)."""
    before = _wf([{"id": "n1", "type": "agent", "name": "N1"}])
    after = _wf([{"id": "n1", "type": "agent", "name": "N2"}])
    changes = workflow_structured_diff(before, after)["nodes"]["changed"][0]["changes"]
    name_change = next(c for c in changes if c["field"] == "name")
    assert "inline" not in name_change


def test_workflow_metadata_change_has_inline():
    """Long metadata values (e.g. description) also get an inline diff."""
    long_a = "描述一" * 10
    long_b = "描述二" * 10
    before = _wf([], name="A")
    before["description"] = long_a
    after = _wf([], name="A")
    after["description"] = long_b
    metadata = workflow_structured_diff(before, after)["metadata"]
    desc = next(m for m in metadata if m["field"] == "description")
    assert "inline" in desc


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
