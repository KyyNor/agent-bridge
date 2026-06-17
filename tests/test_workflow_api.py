from __future__ import annotations

from fastapi.testclient import TestClient


def test_workflow_api_creates_and_lists_workflows(wm_paths):
    from agent_bridge.api.app import create_app
    from agent_bridge.knowledge.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")

    app = create_app(wm_paths, {"root"})
    client = TestClient(app)
    response = client.post(
        "/workflows",
        headers={"X-Agent-Bridge-User": "root"},
        json={
            "workflow_key": "page-report",
            "name": "Page Report",
            "description": "Nightly page report",
            "profile_key": "report-plane",
            "workflow_js": "export const manifest = {};",
            "manifest": {"name": "Page Report", "nodes": [], "edges": [], "schemas": {}},
            "schedule": {"enabled": True, "start_time": "22:00", "stop_time": "07:00"},
            "status": "active",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["workflow_key"] == "page-report"

    listed = client.get("/workflows", headers={"X-Agent-Bridge-User": "root"})
    assert listed.status_code == 200
    assert [item["workflow_key"] for item in listed.json()] == ["page-report"]


def test_workflow_api_lists_artifacts(wm_paths):
    from agent_bridge.api.app import create_app
    from agent_bridge.knowledge.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    svc.workflows.upsert_definition(
        actor="root",
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        workflow_js="",
        manifest={"name": "Page Report", "nodes": [], "edges": [], "schemas": {}},
        status="active",
    )
    svc.workflows.save_artifact(
        workflow_key="page-report",
        profile_key="report-plane",
        run_id="run_1",
        task_key="page:a",
        title="Page A",
        path="reports/page-a/index.md",
        tags=["finance"],
        format="markdown",
        summary="Finance report",
        content="# Page A",
        metadata={},
    )

    client = TestClient(create_app(wm_paths, {"root"}))
    response = client.get(
        "/workflow-artifacts?profile_key=report-plane&query=Page",
        headers={"X-Agent-Bridge-User": "root"},
    )
    assert response.status_code == 200
    assert response.json()["items"][0]["title"] == "Page A"


def test_workflow_api_rejects_non_admin_profile_artifact_query(wm_paths):
    from agent_bridge.api.app import create_app
    from agent_bridge.knowledge.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    svc.workflows.upsert_definition(
        actor="root",
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        workflow_js="",
        manifest={"name": "Page Report", "nodes": [], "edges": [], "schemas": {}},
        status="active",
    )
    svc.workflows.save_artifact(
        workflow_key="page-report",
        profile_key="report-plane",
        run_id="run_1",
        task_key="page:a",
        title="Page A",
        path="reports/page-a/index.md",
        tags=["finance"],
        format="markdown",
        summary="Finance report",
        content="# Page A",
        metadata={},
    )

    client = TestClient(create_app(wm_paths, {"root"}))
    response = client.get(
        "/workflow-artifacts?profile_key=report-plane&query=Page",
        headers={"X-Agent-Bridge-User": "alice"},
    )

    assert response.status_code == 403
    assert "profile context is not trusted" in response.text


def _seed_artifact(svc, content: str = "# Page A\n\nFull body") -> str:
    svc.workflows.upsert_definition(
        actor="root",
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        workflow_js="",
        manifest={"name": "Page Report", "nodes": [], "edges": [], "schemas": {}},
        status="active",
    )
    saved = svc.workflows.save_artifact(
        workflow_key="page-report",
        profile_key="report-plane",
        run_id="run_1",
        task_key="page:a",
        title="Page A",
        path="reports/page-a/index.md",
        tags=["finance"],
        format="markdown",
        summary="Finance report",
        content=content,
        metadata={},
    )
    return saved["artifact_id"]


def test_workflow_api_returns_full_artifact_content(wm_paths):
    from agent_bridge.api.app import create_app
    from agent_bridge.knowledge.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    artifact_id = _seed_artifact(svc)

    client = TestClient(create_app(wm_paths, {"root"}))
    response = client.get(
        f"/workflow-artifacts/{artifact_id}",
        headers={"X-Agent-Bridge-User": "root"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["artifact_id"] == artifact_id
    assert body["title"] == "Page A"
    assert body["path"] == "reports/page-a/index.md"
    assert body["content"] == "# Page A\n\nFull body"
    assert body["tags"] == ["finance"]


def test_workflow_api_rejects_non_admin_artifact_detail_without_trusted_profile(wm_paths):
    from agent_bridge.api.app import create_app
    from agent_bridge.knowledge.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    artifact_id = _seed_artifact(svc)

    client = TestClient(create_app(wm_paths, {"root"}))
    response = client.get(
        f"/workflow-artifacts/{artifact_id}?profile_key=report-plane",
        headers={"X-Agent-Bridge-User": "alice"},
    )

    assert response.status_code == 403
    assert "profile context is not trusted" in response.text


def test_workflow_api_artifact_detail_404_for_unknown_id(wm_paths):
    from agent_bridge.api.app import create_app
    from agent_bridge.knowledge.service import AgentBridgeService

    AgentBridgeService.create(wm_paths, {"root"}).store.init_schema()
    client = TestClient(create_app(wm_paths, {"root"}))
    response = client.get(
        "/workflow-artifacts/artifact_does_not_exist",
        headers={"X-Agent-Bridge-User": "root"},
    )
    assert response.status_code == 404


def _seed_workflow(svc, key: str = "page-report") -> None:
    svc.workflows.upsert_definition(
        actor="root",
        workflow_key=key,
        name="Page Report",
        description="",
        profile_key="report-plane",
        workflow_js="",
        manifest={"name": "Page Report", "nodes": [], "edges": [], "schemas": {}},
        status="active",
    )


def test_workflow_api_lists_runs_for_workflow(wm_paths):
    from agent_bridge.api.app import create_app
    from agent_bridge.knowledge.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    _seed_workflow(svc)
    svc.store.create_workflow_run(
        run_id="run_1", workflow_key="page-report", profile_key="report-plane",
        task_key=None, status="completed", temp_dir="/tmp/run_1",
    )
    svc.store.create_workflow_run(
        run_id="run_2", workflow_key="page-report", profile_key="report-plane",
        task_key=None, status="failed", temp_dir="/tmp/run_2",
    )

    client = TestClient(create_app(wm_paths, {"root"}))
    response = client.get("/workflows/page-report/runs", headers={"X-Agent-Bridge-User": "root"})

    assert response.status_code == 200, response.text
    runs = response.json()
    assert [r["run_id"] for r in runs] == ["run_2", "run_1"]  # newest first
    assert runs[0]["status"] == "failed"
    assert runs[1]["status"] == "completed"


def test_workflow_api_deletes_workflow_and_cascades(wm_paths):
    from agent_bridge.api.app import create_app
    from agent_bridge.knowledge.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    _seed_workflow(svc)
    svc.store.create_workflow_run(
        run_id="run_1", workflow_key="page-report", profile_key="report-plane",
        task_key=None, status="completed", temp_dir="/tmp/run_1",
    )

    client = TestClient(create_app(wm_paths, {"root"}))
    deleted = client.post("/workflows/page-report/delete", headers={"X-Agent-Bridge-User": "root"})
    assert deleted.status_code == 200, deleted.text

    listed = client.get("/workflows", headers={"X-Agent-Bridge-User": "root"})
    assert listed.json() == []

    gone = client.get("/workflows/page-report", headers={"X-Agent-Bridge-User": "root"})
    assert gone.status_code == 404

    runs = client.get("/workflows/page-report/runs", headers={"X-Agent-Bridge-User": "root"})
    assert runs.status_code == 200
    assert runs.json() == []  # runs cascaded away
