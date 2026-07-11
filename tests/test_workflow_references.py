import pytest

from agent_bridge.automation.workflows.definition import EdgeCondition
from agent_bridge.automation.workflows.references import MissingReferenceError, evaluate_condition, render_text, render_value

CONTEXT = {"input": {"limit": 20}, "task": {"payload": {"repo": "acme/demo"}}, "nodes": {"classify": {"output": {"category": "bug", "tags": ["ui"]}}}}


def test_whole_reference_preserves_json_type():
    assert render_value("{{ input.limit }}", CONTEXT) == 20


def test_embedded_reference_becomes_text():
    assert render_text("repo={{ task.payload.repo }}", CONTEXT) == "repo=acme/demo"


def test_missing_prompt_reference_fails():
    with pytest.raises(MissingReferenceError):
        render_text("{{ task.payload.missing }}", CONTEXT)


@pytest.mark.parametrize("operator,expected", [("equals", True), ("not_equals", False), ("exists", True), ("not_exists", False), ("contains", True)])
def test_conditions(operator, expected):
    value = "bug" if operator != "contains" else "ui"
    result = evaluate_condition(EdgeCondition(field="nodes.classify.output.category" if operator != "contains" else "nodes.classify.output.tags", operator=operator, value=value), CONTEXT)
    assert result.matched is expected
