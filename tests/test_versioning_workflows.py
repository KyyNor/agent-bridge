"""Version history + structured diff for workflow definitions."""
from __future__ import annotations

import pytest

from agent_bridge.app.service import AgentBridgeService
from agent_bridge.core.domain import NotFound


def _make_service(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    service.store.upsert_project_profile(
        profile_key="p1", name="P1", created_by="root"
    )
    return service


GET_TASK_NODE = {"id": "n1", "name": "N1", "type": "get_task", "position": {"x": 0, "y": 0}, "config": {}}


def _upsert(service, *, workflow_key="wf", name="W", definition=None, **overrides):
    payload = dict(
        actor="root",
        workflow_key=workflow_key,
        name=name,
        description="",
        profile_key="p1",
        status="active",
        workflow_type="operation",
        definition=definition or {"nodes": [dict(GET_TASK_NODE)], "edges": []},
    )
    payload.update(overrides)
    return service.workflows.upsert_definition(**payload)


def test_first_save_creates_revision_1(wm_paths):
    service = _make_service(wm_paths)
    saved = _upsert(service)
    assert saved["revision_no"] == 1
    assert saved["content_hash"]
    revs = service.workflows.list_revisions("root", "wf")
    assert [r["revision_no"] for r in revs] == [1]


def test_unchanged_save_does_not_create_new_revision(wm_paths):
    service = _make_service(wm_paths)
    _upsert(service)
    again = _upsert(service)
    assert again["revision_no"] == 1


def test_structured_diff_reports_added_node_and_edge(wm_paths):
    service = _make_service(wm_paths)
    # Provide a real script node target so the graph still validates.
    service.scripts.upsert_script(
        actor="root", script_key="helper", name="H", description="",
        language="python", code="def main(e):\n    return {}\n",
        input_schema={"type": "object", "properties": {}},
        status="active", owner_type="system", owner_key="",
    )
    graph_v1 = {"nodes": [dict(GET_TASK_NODE)], "edges": []}
    _upsert(service, definition=graph_v1)

    script_node = {
        "id": "n2", "name": "N2", "type": "script", "position": {"x": 1, "y": 0},
        "config": {"script_key": "helper"},
    }
    graph_v2 = {
        "nodes": [dict(GET_TASK_NODE), script_node],
        "edges": [{"id": "e1", "source": "n1", "target": "n2"}],
    }
    v2 = _upsert(service, name="W2", definition=graph_v2)
    assert v2["revision_no"] == 2

    diff = service.workflows.diff_revisions("root", "wf", from_no=1, to_no=2)
    structured = diff["structured"]
    assert structured["identical"] is False
    assert [n["id"] for n in structured["nodes"]["added"]] == ["n2"]
    assert [e["id"] for e in structured["edges"]["added"]] == ["e1"]
    assert any(m["field"] == "name" for m in structured["metadata"])


def test_get_revision_404_for_unknown(wm_paths):
    service = _make_service(wm_paths)
    _upsert(service)
    with pytest.raises(NotFound):
        service.workflows.get_revision("root", "wf", 999)


def test_diff_text_present(wm_paths):
    service = _make_service(wm_paths)
    _upsert(service, name="First")
    _upsert(service, name="Second")
    diff = service.workflows.diff_revisions("root", "wf", from_no=1, to_no=2)
    assert diff["text"]["identical"] is False
    assert "First" in diff["text"]["content"]
    assert "Second" in diff["text"]["content"]
