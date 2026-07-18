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


def test_workflow_revision_records_edit_source(wm_paths):
    service = _make_service(wm_paths)
    _upsert(service)
    assert service.workflows.get_revision("root", "wf", 1)["source"] == "edit"


def test_workflow_revision_source_can_be_selected(wm_paths):
    service = _make_service(wm_paths)
    service.workflows.upsert_definition(
        actor="root",
        workflow_key="wf",
        name="W",
        description="",
        profile_key="p1",
        status="active",
        workflow_type="operation",
        definition={"nodes": [dict(GET_TASK_NODE)], "edges": []},
        revision_source="import",
    )
    assert service.workflows.get_revision("root", "wf", 1)["source"] == "import"


def test_unchanged_save_does_not_create_new_revision(wm_paths):
    service = _make_service(wm_paths)
    _upsert(service)
    again = _upsert(service)
    assert again["revision_no"] == 1


def test_restore_revision_appends_new_restore_revision(wm_paths):
    service = _make_service(wm_paths)
    _upsert(service, name="v1")
    _upsert(service, name="v2")

    restored = service.workflows.restore_revision("root", "wf", 1)

    assert restored["revision_no"] == 3
    assert restored["name"] == "v1"
    assert restored["restored_from_revision"] == 1
    assert service.workflows.get_revision("root", "wf", 3)["source"] == "restore"


def test_restore_same_current_content_does_not_create_duplicate_revision(wm_paths):
    service = _make_service(wm_paths)
    _upsert(service, name="v1")

    restored = service.workflows.restore_revision("root", "wf", 1)

    assert restored["revision_no"] == 1
    assert restored["restored_from_revision"] == 1
    assert service.workflows.list_revisions("root", "wf")[0]["source"] == "edit"


def test_export_contains_current_definition_but_not_execution_data(wm_paths):
    service = _make_service(wm_paths)
    _upsert(service)

    exported = service.workflows.export_definition("root", "wf")

    assert exported["format"] == "agent-bridge.workflow"
    assert exported["format_version"] == 1
    assert exported["workflow"]["workflow_key"] == "wf"
    assert exported["revision"]["source"] == "edit"
    assert "runs" not in exported
    assert "artifacts" not in exported


def test_workflow_position_only_change_does_not_create_revision(wm_paths):
    service = _make_service(wm_paths)
    _upsert(service, definition={"nodes": [dict(GET_TASK_NODE)], "edges": []})
    moved = dict(GET_TASK_NODE)
    moved["position"] = {"x": 480, "y": 160}
    saved = _upsert(service, definition={"nodes": [moved], "edges": []})
    assert saved["revision_no"] == 1


def test_workflow_save_rolls_back_when_revision_archive_fails(wm_paths, monkeypatch):
    service = _make_service(wm_paths)

    def fail_revision(**kwargs):
        raise RuntimeError("revision archive failed")

    monkeypatch.setattr(service.store.workflows, "create_definition_revision", fail_revision)
    with pytest.raises(RuntimeError, match="revision archive failed"):
        _upsert(service)

    assert service.store.get_workflow_definition("wf") is None
    assert service.store.workflows.list_definition_revisions("wf") == []


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


def test_corrupt_workflow_revision_snapshot_is_not_silently_empty(wm_paths):
    service = _make_service(wm_paths)
    _upsert(service)
    with service.store.connect() as conn:
        conn.execute(
            "UPDATE workflow_definition_revisions SET snapshot_json = ? WHERE workflow_key = ? AND revision_no = ?",
            ("{not-json", "wf", 1),
        )

    with pytest.raises(ValueError, match="corrupt workflow revision snapshot"):
        service.workflows.get_revision("root", "wf", 1)


def test_diff_text_present(wm_paths):
    service = _make_service(wm_paths)
    _upsert(service, name="First")
    _upsert(service, name="Second")
    diff = service.workflows.diff_revisions("root", "wf", from_no=1, to_no=2)
    assert diff["text"]["identical"] is False
    assert "First" in diff["text"]["content"]
    assert "Second" in diff["text"]["content"]
