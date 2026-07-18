from __future__ import annotations

import json

from fastapi.testclient import TestClient

from agent_bridge.api.app import create_app
from agent_bridge.app.service import AgentBridgeService


GET_TASK_NODE = {
    "id": "n1",
    "name": "N1",
    "type": "get_task",
    "position": {"x": 0, "y": 0},
    "config": {},
}


def _setup(wm_paths) -> tuple[AgentBridgeService, TestClient]:
    service = AgentBridgeService.create(wm_paths, {"root"})
    service.store.upsert_project_profile(profile_key="p1", name="P1", created_by="root")
    client = TestClient(create_app(paths=wm_paths, admins={"root"}))
    return service, client


def _save(service: AgentBridgeService, name: str) -> dict:
    return service.workflows.upsert_definition(
        actor="root",
        workflow_key="wf",
        name=name,
        description="",
        profile_key="p1",
        status="active",
        workflow_type="operation",
        definition={"nodes": [dict(GET_TASK_NODE)], "edges": []},
    )


def test_restore_revision_endpoint_appends_restore_revision(wm_paths):
    service, client = _setup(wm_paths)
    _save(service, "v1")
    _save(service, "v2")

    response = client.post(
        "/workflows/wf/revisions/1/restore",
        headers={"X-Agent-Bridge-User": "root"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revision_no"] == 3
    assert body["name"] == "v1"
    assert body["restored_from_revision"] == 1
    assert body["revision_source"] == "restore"
    assert service.workflows.get_revision("root", "wf", 3)["source"] == "restore"


def test_export_endpoint_returns_downloadable_json_envelope(wm_paths):
    service, client = _setup(wm_paths)
    _save(service, "v1")

    response = client.get(
        "/workflows/wf/export",
        headers={"X-Agent-Bridge-User": "root"},
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["content-disposition"] == 'attachment; filename="wf.workflow.json"'
    payload = json.loads(response.content)
    assert payload["format"] == "agent-bridge.workflow"
    assert payload["workflow"]["name"] == "v1"
    assert payload["revision"]["source"] == "edit"


def test_import_api_creates_new_workflow_after_preview_and_confirm(wm_paths):
    service, client = _setup(wm_paths)
    exported = {
        "format": "agent-bridge.workflow",
        "format_version": 1,
        "workflow": {
            "workflow_key": "imported",
            "name": "Imported",
            "description": "",
            "profile_key": "p1",
            "status": "active",
            "workflow_type": "operation",
            "definition": {"nodes": [dict(GET_TASK_NODE)], "edges": []},
        },
    }

    preview = client.post(
        "/workflows/import/preview",
        headers={"X-Agent-Bridge-User": "root"},
        files={"file": ("import.workflow.json", json.dumps(exported).encode(), "application/json")},
    )
    assert preview.status_code == 200, preview.text
    preview_body = preview.json()
    assert preview_body["operation"] == "create"

    confirmed = client.post(
        "/workflows/import/confirm",
        headers={"X-Agent-Bridge-User": "root"},
        json={"import_id": preview_body["import_id"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["workflow_key"] == "imported"
    assert confirmed.json()["revision_source"] == "import"
    assert service.workflows.get_revision("root", "imported", 1)["source"] == "import"


def test_import_api_overwrite_preview_returns_diff(wm_paths):
    service, client = _setup(wm_paths)
    _save(service, "old")
    exported = {
        "format": "agent-bridge.workflow",
        "format_version": 1,
        "workflow": {
            "workflow_key": "wf",
            "name": "incoming",
            "description": "",
            "profile_key": "p1",
            "status": "active",
            "workflow_type": "operation",
            "definition": {"nodes": [dict(GET_TASK_NODE)], "edges": []},
        },
    }

    preview = client.post(
        "/workflows/import/preview",
        headers={"X-Agent-Bridge-User": "root"},
        data={"target_workflow_key": "wf", "target_mode": "overwrite"},
        files={"file": ("import.workflow.json", json.dumps(exported).encode(), "application/json")},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["operation"] == "overwrite"
    assert preview.json()["diff"]["structured"]["identical"] is False
