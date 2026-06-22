from __future__ import annotations

from fastapi.testclient import TestClient


SCRIPT_CODE = """
def main(envelope):
    return {
        "profile_key": envelope["profile_key"],
        "workflow": envelope["workflow"],
    }
"""


def _create_client(wm_paths):
    from agent_bridge.api.app import create_app

    app = create_app(wm_paths, {"root"})
    return TestClient(app)


def _register_script(client: TestClient) -> None:
    response = client.post(
        "/scripts",
        headers={"X-Agent-Bridge-User": "root"},
        json={
            "script_key": "system.ctx_echo",
            "name": "Context Echo",
            "description": "",
            "language": "python",
            "code": SCRIPT_CODE,
            "status": "active",
            "owner_type": "system",
            "owner_key": "",
        },
    )
    assert response.status_code == 200


def test_script_test_route_injects_profile_and_workflow_headers(wm_paths):
    from agent_bridge.app.service import AgentBridgeService

    client = _create_client(wm_paths)
    svc: AgentBridgeService = client.app.state.agent_bridge_service
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    svc.workflows.upsert_definition(
        actor="root",
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        workflow_js="",
        status="active",
    )
    svc.store.create_workflow_run(
        run_id="run_1",
        workflow_key="page-report",
        profile_key="report-plane",
        task_key=None,
        status="running",
        temp_dir="/tmp/workflow-run",
    )
    _register_script(client)

    response = client.post(
        "/scripts/system.ctx_echo/test",
        headers={
            "X-Agent-Bridge-User": "root",
            "X-Agent-Bridge-MetaMCP-Profile": "report-plane",
            "X-Agent-Bridge-Workflow": "true",
            "X-Agent-Bridge-Workflow-Key": "page-report",
            "X-Agent-Bridge-Workflow-Run-Id": "run_1",
        },
        json={"params": {"limit": 3}, "timeout_seconds": 10},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["result"]["profile_key"] == "report-plane"
    assert payload["result"]["workflow"] == {
        "enabled": True,
        "workflow_key": "page-report",
        "run_id": "run_1",
    }


def test_runtime_workflow_routes_require_complete_headers(wm_paths):
    client = _create_client(wm_paths)

    response = client.post(
        "/runtime/workflow/get-task",
        headers={
            "X-Agent-Bridge-User": "root",
            "X-Agent-Bridge-MetaMCP-Profile": "report-plane",
            "X-Agent-Bridge-Workflow": "true",
        },
        json={},
    )

    assert response.status_code == 400
    assert "workflow context is required" in response.text


def test_runtime_workflow_routes_use_trusted_header_context(wm_paths):
    from agent_bridge.app.service import AgentBridgeService

    client = _create_client(wm_paths)
    svc: AgentBridgeService = client.app.state.agent_bridge_service
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    svc.workflows.upsert_definition(
        actor="root",
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        workflow_js="",
        status="active",
    )
    svc.store.create_workflow_run(
        run_id="run_1",
        workflow_key="page-report",
        profile_key="report-plane",
        task_key=None,
        status="running",
        temp_dir="/tmp/workflow-run",
    )

    set_response = client.post(
        "/runtime/workflow/set-task",
        headers={
            "X-Agent-Bridge-User": "root",
            "X-Agent-Bridge-MetaMCP-Profile": "report-plane",
            "X-Agent-Bridge-Workflow": "true",
            "X-Agent-Bridge-Workflow-Key": "page-report",
            "X-Agent-Bridge-Workflow-Run-Id": "run_1",
        },
        json={"tasks": [{"task_key": "page:a", "payload": {"page": "a"}}]},
    )
    assert set_response.status_code == 200
    assert set_response.json()["created"] == 1

    get_response = client.post(
        "/runtime/workflow/get-task",
        headers={
            "X-Agent-Bridge-User": "root",
            "X-Agent-Bridge-MetaMCP-Profile": "report-plane",
            "X-Agent-Bridge-Workflow": "true",
            "X-Agent-Bridge-Workflow-Key": "page-report",
            "X-Agent-Bridge-Workflow-Run-Id": "run_1",
        },
        json={},
    )
    assert get_response.status_code == 200
    assert get_response.json()["task"]["lease_run_id"] == "run_1"

    log_response = client.post(
        "/runtime/workflow/run-log",
        headers={
            "X-Agent-Bridge-User": "root",
            "X-Agent-Bridge-MetaMCP-Profile": "report-plane",
            "X-Agent-Bridge-Workflow": "true",
            "X-Agent-Bridge-Workflow-Key": "page-report",
            "X-Agent-Bridge-Workflow-Run-Id": "run_1",
        },
        json={"level": "info", "stage": "lease", "message": "leased task", "task_key": "page:a", "payload": {}},
    )
    assert log_response.status_code == 200
    assert log_response.json() == {"ok": True}

    tool_logs = svc.governance.list_logs(actor="root", entrypoint="runtime_workflow")
    assert [item["tool_name"] for item in tool_logs] == [
        "workflow_run_log",
        "workflow_get_task",
        "workflow_set_task",
    ]
    assert all(item["source_key"] == "workflow" for item in tool_logs)


def test_script_test_route_accepts_legacy_script_params_body(wm_paths):
    client = _create_client(wm_paths)
    _register_script(client)

    response = client.post(
        "/scripts/system.ctx_echo/test",
        headers={"X-Agent-Bridge-User": "root"},
        json={"script_params": {"limit": 3}, "timeout_seconds": 10},
    )

    assert response.status_code == 200
    assert response.json()["result"]["profile_key"] is None
