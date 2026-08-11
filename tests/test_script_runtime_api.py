from __future__ import annotations

from fastapi.testclient import TestClient

from agent_bridge.automation.workflows.runtime_capability import WORKFLOW_CAPABILITY_HEADER


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
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": True},
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
    assert payload["params"]["workflow"] == {
        "enabled": True,
        "workflow_key": "page-report",
        "run_id": "run_1",
    }


def test_runtime_workflow_set_task_keeps_workflows_isolated_and_accepts_large_batches(wm_paths):
    from agent_bridge.app.service import AgentBridgeService

    client = _create_client(wm_paths)
    svc: AgentBridgeService = client.app.state.agent_bridge_service
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    for workflow_key, run_id in (("workflow-a", "run-a"), ("workflow-b", "run-b")):
        svc.workflows.upsert_definition(
            actor="root",
            workflow_key=workflow_key,
            name=workflow_key,
            description="",
            profile_key="report-plane",
            status="active",
        )
        svc.store.create_workflow_run(
            run_id=run_id,
            workflow_key=workflow_key,
            profile_key="report-plane",
            task_key=None,
            status="running",
            temp_dir=f"/tmp/{run_id}",
        )

    def set_tasks(workflow_key: str, run_id: str):
        return client.post(
            "/runtime/workflow/set-task",
            headers={
                "X-Agent-Bridge-User": "root",
                "X-Agent-Bridge-MetaMCP-Profile": "report-plane",
                "X-Agent-Bridge-Workflow": "true",
                "X-Agent-Bridge-Workflow-Key": workflow_key,
                "X-Agent-Bridge-Workflow-Run-Id": run_id,
            },
            json={"tasks": [{"task_key": f"task-{i}", "payload": {"workflow": workflow_key}} for i in range(200)]},
        )

    first = set_tasks("workflow-a", "run-a")
    second = set_tasks("workflow-b", "run-b")
    assert first.status_code == 200
    assert second.status_code == 200
    first_payload = first.json()
    second_payload = second.json()
    assert first_payload["received"] == 200
    assert second_payload["received"] == 200
    assert first_payload["action_total"] == 200
    assert second_payload["action_total"] == 200
    assert first_payload["unique_task_keys"] == 200
    assert second_payload["unique_task_keys"] == 200
    assert first_payload["unique_task_pairs"] == 200
    assert second_payload["unique_task_pairs"] == 200
    assert first_payload["duplicate_task_key_rows"] == 0
    assert second_payload["duplicate_task_key_rows"] == 0
    assert first_payload["duplicate_task_pair_rows"] == 0
    assert second_payload["duplicate_task_pair_rows"] == 0
    assert first_payload["empty_task_version_count"] == 200
    assert second_payload["empty_task_version_count"] == 200
    assert len(svc.store.list_workflow_tasks("workflow-a")) == 200
    assert len(svc.store.list_workflow_tasks("workflow-b")) == 200
    assert svc.store.get_workflow_task("workflow-a", "task-0")["payload"] == {"workflow": "workflow-a"}
    assert svc.store.get_workflow_task("workflow-b", "task-0")["payload"] == {"workflow": "workflow-b"}


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


def test_runtime_workflow_route_rejects_forged_group_and_accepts_capability(wm_paths):
    client = _create_client(wm_paths)
    svc = client.app.state.agent_bridge_service
    svc.store.init_schema()
    svc.access.bootstrap_admin_memberships()
    svc.access.upsert_group(actor="root", group_key="team-a", name="A 组")
    svc.access.upsert_group(actor="root", group_key="team-b", name="B 组")
    svc.access.set_user_group(actor="root", user_id="alice", group_key="team-a")
    svc.access.set_user_group(actor="root", user_id="bob", group_key="team-b")
    svc.governance.upsert_profile(
        actor="alice",
        profile_key="team-a-profile",
        name="A 组能力平面",
        description="",
        status="active",
    )
    svc.workflows.upsert_definition(
        actor="alice",
        workflow_key="team-a-workflow",
        name="A 组工作流",
        description="",
        profile_key="team-a-profile",
        definition={"nodes": [], "edges": []},
        status="active",
    )
    run = svc.store.create_workflow_run(
        run_id="team-a-run",
        workflow_key="team-a-workflow",
        profile_key="team-a-profile",
        task_key=None,
        status="running",
        temp_dir="/tmp/team-a-run",
    )
    context_headers = {
        "X-Agent-Bridge-MetaMCP-Profile": "team-a-profile",
        "X-Agent-Bridge-Workflow": "true",
        "X-Agent-Bridge-Workflow-Key": "team-a-workflow",
        "X-Agent-Bridge-Workflow-Run-Id": "team-a-run",
    }

    forged = client.post(
        "/runtime/workflow/set-task",
        headers={**context_headers, "X-Agent-Bridge-User": "bob"},
        json={"tasks": [{"task_key": "forged", "payload": {}}]},
    )

    assert forged.status_code == 403
    assert svc.store.get_workflow_task("team-a-workflow", "forged") is None

    capability = svc.workflows.issue_runtime_capability(run=run, initiated_by="root")
    trusted = client.post(
        "/runtime/workflow/set-task",
        headers={
            **context_headers,
            "X-Agent-Bridge-User": capability.actor,
            WORKFLOW_CAPABILITY_HEADER: capability.token,
        },
        json={"tasks": [{"task_key": "trusted", "payload": {}}]},
    )

    assert trusted.status_code == 200
    assert svc.store.get_workflow_task("team-a-workflow", "trusted") is not None
    logs = svc.governance.list_logs(actor="root", entrypoint="runtime_workflow")
    assert logs[0]["owner_group_key"] == "team-a"


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


def test_script_reset_route_restores_builtin_default(wm_paths):
    from agent_bridge.app.service import AgentBridgeService

    client = _create_client(wm_paths)
    svc: AgentBridgeService = client.app.state.agent_bridge_service
    svc.store.init_schema()
    default = svc.scripts.get_script("root", "system.validate_workflow")

    override_response = client.post(
        "/scripts",
        headers={"X-Agent-Bridge-User": "root"},
        json={
            "script_key": "system.validate_workflow",
            "name": "ignored",
            "description": "ignored",
            "language": "python",
            "code": "def main(envelope):\n    return {'valid': True, 'errors': [], 'warnings': [{'source': 'override'}]}\n",
            "input_schema": default["input_schema"],
            "output_schema": default["output_schema"],
            "status": "active",
            "owner_type": "system",
            "owner_key": "",
        },
    )
    assert override_response.status_code == 200
    assert override_response.json()["source"] == "database"

    response = client.post(
        "/scripts/system.validate_workflow/reset",
        headers={"X-Agent-Bridge-User": "root"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["script_key"] == "system.validate_workflow"
    assert payload["source"] == "default"
