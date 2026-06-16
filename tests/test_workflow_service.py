from __future__ import annotations


def _service(wm_paths):
    from agent_bridge.knowledge.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    return svc


def test_workflow_service_creates_definition_with_existing_profile(wm_paths):
    svc = _service(wm_paths)

    created = svc.workflows.upsert_definition(
        actor="root",
        workflow_key="page-report",
        name="Page Report",
        description="Nightly page report",
        profile_key="report-plane",
        workflow_js="export const manifest = {};",
        manifest={"name": "Page Report", "nodes": [], "edges": [], "schemas": {}},
        schedule={"enabled": True, "start_time": "22:00", "stop_time": "07:00"},
        status="active",
    )

    assert created["workflow_key"] == "page-report"
    assert created["profile_key"] == "report-plane"


def test_workflow_service_rejects_missing_profile(wm_paths):
    from agent_bridge.core.domain import ValidationError

    svc = _service(wm_paths)

    try:
        svc.workflows.upsert_definition(
            actor="root",
            workflow_key="page-report",
            name="Page Report",
            description="",
            profile_key="missing",
            workflow_js="",
            manifest={"name": "Page Report", "nodes": [], "edges": [], "schemas": {}},
            schedule={"enabled": True, "start_time": "22:00", "stop_time": "07:00"},
            status="active",
        )
    except ValidationError as exc:
        assert "profile not found" in exc.message
    else:
        raise AssertionError("missing profile should be rejected")


def test_workflow_service_appends_run_log(wm_paths):
    svc = _service(wm_paths)
    svc.workflows.upsert_definition(
        actor="root",
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        workflow_js="",
        manifest={"name": "Page Report", "nodes": [], "edges": [], "schemas": {}},
        schedule={"enabled": True, "start_time": "22:00", "stop_time": "07:00"},
        status="active",
    )
    svc.store.create_workflow_run(
        run_id="run_1",
        workflow_key="page-report",
        profile_key="report-plane",
        task_key=None,
        status="running",
        temp_dir="/tmp/run_1",
    )

    svc.workflows.append_run_log(
        workflow_key="page-report",
        run_id="run_1",
        task_key="page:a",
        level="info",
        stage="analyze",
        message="started",
        payload={"step": 1},
    )

    logs = svc.workflows.list_run_logs("root", "run_1")
    assert logs[0]["message"] == "started"
    assert logs[0]["payload"]["step"] == 1


def test_workflow_service_saves_and_searches_artifacts(wm_paths):
    svc = _service(wm_paths)
    svc.workflows.upsert_definition(
        actor="root",
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        workflow_js="",
        manifest={"name": "Page Report", "nodes": [], "edges": [], "schemas": {}},
        schedule={"enabled": True, "start_time": "22:00", "stop_time": "07:00"},
        status="active",
    )

    saved = svc.workflows.save_artifact(
        workflow_key="page-report",
        profile_key="report-plane",
        run_id="run_1",
        task_key="page:a",
        title="Page A Report",
        path="reports/page-a/index.md",
        tags=["report", "finance"],
        format="markdown",
        summary="Finance page report",
        content="# Page A\n\nUses table finance_orders.",
        metadata={"page_key": "page-a"},
    )

    assert saved["artifact_id"].startswith("artifact_")
    results = svc.workflows.search_artifacts(
        actor="root",
        profile_key="report-plane",
        query="finance_orders",
        tags=["finance"],
        path="reports/",
        workflow_key=None,
        limit=10,
    )
    assert [item["title"] for item in results["items"]] == ["Page A Report"]
    assert "finance_orders" in results["items"][0]["snippet"]


def test_workflow_service_allows_non_admin_profile_artifact_search(wm_paths):
    svc = _service(wm_paths)
    svc.workflows.upsert_definition(
        actor="root",
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        workflow_js="",
        manifest={"name": "Page Report", "nodes": [], "edges": [], "schemas": {}},
        schedule={"enabled": True, "start_time": "22:00", "stop_time": "07:00"},
        status="active",
    )
    svc.workflows.save_artifact(
        workflow_key="page-report",
        profile_key="report-plane",
        run_id="run_1",
        task_key="page:a",
        title="Page A Report",
        path="reports/page-a/index.md",
        tags=["report", "finance"],
        format="markdown",
        summary="Finance page report",
        content="# Page A\n\nUses table finance_orders.",
        metadata={"page_key": "page-a"},
    )

    results = svc.workflows.search_artifacts(
        actor="alice",
        profile_key="report-plane",
        query="finance_orders",
        tags=[],
        path=None,
        workflow_key=None,
        limit=10,
    )

    assert [item["title"] for item in results["items"]] == ["Page A Report"]


def test_workflow_service_rejects_non_admin_artifact_search_without_profile(wm_paths):
    from agent_bridge.core.domain import ValidationError

    svc = _service(wm_paths)

    try:
        svc.workflows.search_artifacts(
            actor="alice",
            profile_key=None,
            query="anything",
            tags=[],
            path=None,
            workflow_key=None,
            limit=10,
        )
    except ValidationError as exc:
        assert "profile_key is required" in exc.message
    else:
        raise AssertionError("non-admin artifact search without profile should fail")
