from __future__ import annotations

from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook


def _task_import_workbook_bytes(
    rows: list[list[object]],
    *,
    headers: list[object] | None = None,
) -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "tasks"
    worksheet.append(headers or ["task_key", "task_version", "type"])
    for row in rows:
        worksheet.append(row)
    output = BytesIO()
    try:
        workbook.save(output)
        return output.getvalue()
    finally:
        workbook.close()


def _malformed_worksheet_workbook_bytes() -> bytes:
    source_bytes = _task_import_workbook_bytes([])
    output = BytesIO()
    with ZipFile(BytesIO(source_bytes)) as source, ZipFile(output, "w", ZIP_DEFLATED) as target:
        for name in source.namelist():
            data = source.read(name)
            if name == "xl/worksheets/sheet1.xml":
                data = b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><dimension ref="A1:C1"/><sheetData>'
            target.writestr(name, data)
    return output.getvalue()


def test_workflow_api_creates_and_lists_workflows(wm_paths):
    from agent_bridge.api.app import create_app
    from agent_bridge.app.service import AgentBridgeService

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
            "workflow_js": "export default async function workflow() {}",
            "schedule": {"enabled": True, "start_time": "22:00", "stop_time": "07:00"},
            "status": "active",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["workflow_key"] == "page-report"
    assert "manifest" not in response.json()

    listed = client.get("/workflows", headers={"X-Agent-Bridge-User": "root"})
    assert listed.status_code == 200
    assert [item["workflow_key"] for item in listed.json()] == ["page-report"]
    assert "manifest" not in listed.json()[0]


def test_workflow_api_can_list_more_than_default_twenty_runs(wm_paths):
    from agent_bridge.api.app import create_app
    from agent_bridge.app.service import AgentBridgeService

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
        status="active",
    )
    for i in range(75):
        svc.store.create_workflow_run(
            run_id=f"run_{i:02d}",
            workflow_key="page-report",
            profile_key="report-plane",
            task_key=None,
            status="completed",
            temp_dir="",
        )

    client = TestClient(create_app(wm_paths, {"root"}))
    response = client.get(
        "/workflows/page-report/runs?limit=75",
        headers={"X-Agent-Bridge-User": "root"},
    )

    assert response.status_code == 200, response.text
    assert len(response.json()) == 75


def test_workflow_api_lists_artifacts(wm_paths):
    from agent_bridge.api.app import create_app
    from agent_bridge.app.service import AgentBridgeService

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


def test_workflow_api_paginates_artifacts_with_total_and_offset(wm_paths):
    from agent_bridge.api.app import create_app
    from agent_bridge.app.service import AgentBridgeService

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
        status="active",
    )
    for index in range(3):
        svc.workflows.save_artifact(
            workflow_key="page-report",
            profile_key="report-plane",
            run_id=f"run_{index}",
            task_key=f"page:{index}",
            title=f"Page {index}",
            path=f"pages/{index}.md",
            tags=["finance"],
            format="markdown",
            summary="Finance report",
            content=f"# Page {index}",
            metadata={},
        )

    client = TestClient(create_app(wm_paths, {"root"}))
    response = client.get(
        "/workflow-artifacts?profile_key=report-plane&workflow_key=page-report&limit=1&offset=1",
        headers={"X-Agent-Bridge-User": "root"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["total"] == 3
    assert response.json()["limit"] == 1
    assert response.json()["offset"] == 1
    assert [item["task_key"] for item in response.json()["items"]] == ["page:1"]


def test_workflow_api_artifact_page_crosses_the_original_thirty_row_limit(wm_paths):
    from agent_bridge.api.app import create_app
    from agent_bridge.app.service import AgentBridgeService

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
        status="active",
    )
    for index in range(31):
        svc.workflows.save_artifact(
            workflow_key="page-report",
            profile_key="report-plane",
            run_id=f"run_{index}",
            task_key=f"page:{index}",
            title=f"Page {index}",
            path=f"pages/{index}.md",
            tags=["finance"],
            format="markdown",
            summary="Finance report",
            content=f"# Page {index}",
            metadata={},
        )

    client = TestClient(create_app(wm_paths, {"root"}))
    body = client.get(
        "/workflow-artifacts",
        params={"workflow_key": "page-report", "limit": 1, "offset": 30},
        headers={"X-Agent-Bridge-User": "root"},
    ).json()

    assert body["total"] == 31
    assert body["limit"] == 1
    assert body["offset"] == 30
    assert len(body["items"]) == 1


def test_workflow_api_lists_current_artifacts_and_version_history(wm_paths):
    from agent_bridge.api.app import create_app
    from agent_bridge.app.service import AgentBridgeService

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
        status="active",
    )
    svc.workflows.save_artifact(
        workflow_key="page-report",
        profile_key="report-plane",
        run_id="run_1",
        task_key="page:a",
        task_version="v1",
        title="Page A v1",
        path="pages/page-a.md",
        tags=["finance"],
        format="markdown",
        summary="v1",
        content="# v1",
        metadata={},
    )
    svc.workflows.save_artifact(
        workflow_key="page-report",
        profile_key="report-plane",
        run_id="run_2",
        task_key="page:a",
        task_version="v2",
        title="Page A v2",
        path="pages/page-a.md",
        tags=["finance"],
        format="markdown",
        summary="v2",
        content="# v2",
        metadata={},
    )

    client = TestClient(create_app(wm_paths, {"root"}))
    current = client.get(
        "/workflow-artifacts?profile_key=report-plane&workflow_key=page-report&task_key=page:a",
        headers={"X-Agent-Bridge-User": "root"},
    )
    assert current.status_code == 200, current.text
    assert [item["task_version"] for item in current.json()["items"]] == ["v2"]
    assert current.json()["items"][0]["is_current"] is True

    history = client.get(
        "/workflow-artifacts/history?profile_key=report-plane&workflow_key=page-report&task_key=page:a",
        headers={"X-Agent-Bridge-User": "root"},
    )
    assert history.status_code == 200, history.text
    assert [item["task_version"] for item in history.json()["versions"]] == ["v2", "v1"]
    assert history.json()["versions"][0]["is_current"] is True
    assert history.json()["versions"][0]["artifacts"][0]["content"] == "# v2"
    assert history.json()["versions"][1]["artifacts"][0]["content"] == "# v1"


def test_workflow_api_rejects_non_admin_profile_artifact_query(wm_paths):
    from agent_bridge.api.app import create_app
    from agent_bridge.app.service import AgentBridgeService

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
    from agent_bridge.app.service import AgentBridgeService

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
    from agent_bridge.app.service import AgentBridgeService

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
    from agent_bridge.app.service import AgentBridgeService

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
        status="active",
    )


def _workflow_import_client(wm_paths, workflow_keys: tuple[str, ...] = ("page-report",)):
    from agent_bridge.api.app import create_app
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    for workflow_key in workflow_keys:
        _seed_workflow(svc, workflow_key)
    return svc, TestClient(create_app(wm_paths, {"root"}))


def test_workflow_api_previews_and_confirms_task_import_as_admin(wm_paths):
    svc, client = _workflow_import_client(wm_paths)
    workbook_bytes = _task_import_workbook_bytes([["task:new", "v1", "repo"]])

    preview_response = client.post(
        "/workflows/page-report/tasks/import/preview",
        headers={"X-Agent-Bridge-User": "root"},
        files={
            "file": (
                "tasks.xlsx",
                workbook_bytes,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )

    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["can_confirm"] is True
    assert preview["summary"]["created"] == 1
    assert preview["summary"]["total_rows"] == 1
    assert svc.store.get_workflow_task_import(preview["import_id"])["tasks"] == [
        {"payload": {}, "task_key": "task:new", "task_version": "v1", "type": "repo"}
    ]

    confirm_response = client.post(
        "/workflows/page-report/tasks/import/confirm",
        headers={"X-Agent-Bridge-User": "root"},
        json={"import_id": preview["import_id"]},
    )

    assert confirm_response.status_code == 200, confirm_response.text
    assert confirm_response.json()["created"] == 1
    assert svc.store.get_workflow_task("page-report", "task:new", task_version="v1")["status"] == "pending"


def test_workflow_api_task_import_row_errors_disable_confirmation(wm_paths):
    svc, client = _workflow_import_client(wm_paths)
    response = client.post(
        "/workflows/page-report/tasks/import/preview",
        headers={"X-Agent-Bridge-User": "root"},
        files={"file": ("tasks.xlsx", _task_import_workbook_bytes([["task:valid", "v1", "repo"], ["", "v1", "repo"]]))},
    )

    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["can_confirm"] is False
    assert preview["summary"]["valid_rows"] == 1
    assert preview["summary"]["invalid_rows"] == 1
    assert preview["rows"][1]["action"] == "error"
    assert preview["rows"][1]["errors"] == ["task_key 不能为空"]
    assert svc.store.get_workflow_task_import(preview["import_id"])["tasks"] == [
        {"payload": {}, "task_key": "task:valid", "task_version": "v1", "type": "repo"}
    ]

    confirm_response = client.post(
        "/workflows/page-report/tasks/import/confirm",
        headers={"X-Agent-Bridge-User": "root"},
        json={"import_id": preview["import_id"]},
    )
    assert confirm_response.status_code == 400, confirm_response.text
    assert svc.store.get_workflow_task("page-report", "task:valid", task_version="v1") is None


def test_workflow_api_rejects_duplicate_task_key_and_version_confirmation(wm_paths):
    svc, client = _workflow_import_client(wm_paths)
    headers = {"X-Agent-Bridge-User": "root"}
    preview_response = client.post(
        "/workflows/page-report/tasks/import/preview",
        headers=headers,
        files={
            "file": (
                "tasks.xlsx",
                _task_import_workbook_bytes(
                    [
                        ["task:duplicate", "v1", "repo"],
                        ["task:duplicate", "v1", "repo"],
                    ]
                ),
            )
        },
    )

    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["can_confirm"] is False
    assert preview["rows"][0]["action"] == "created"
    assert preview["rows"][0]["errors"] == []
    assert preview["rows"][1]["action"] == "error"
    assert preview["rows"][1]["errors"] == ["task_key + task_version 重复"]

    confirm_response = client.post(
        "/workflows/page-report/tasks/import/confirm",
        headers=headers,
        json={"import_id": preview["import_id"]},
    )

    assert confirm_response.status_code == 400, confirm_response.text
    assert svc.store.get_workflow_task("page-report", "task:duplicate", task_version="v1") is None
    assert svc.store.get_workflow_task_import(preview["import_id"])["status"] == "previewed"


@pytest.mark.parametrize(
    ("filename", "content_kind"),
    [
        ("tasks.csv", "valid_workbook"),
        ("tasks.xlsx", "plain_text"),
        ("tasks.xlsx", "malformed_workbook"),
    ],
)
def test_workflow_api_rejects_invalid_task_import_files(wm_paths, filename, content_kind):
    _svc, client = _workflow_import_client(wm_paths)
    if content_kind == "valid_workbook":
        content = _task_import_workbook_bytes([["task:new", "v1", "repo"]])
    elif content_kind == "plain_text":
        content = b"not an xlsx"
    else:
        content = _malformed_worksheet_workbook_bytes()
    response = client.post(
        "/workflows/page-report/tasks/import/preview",
        headers={"X-Agent-Bridge-User": "root"},
        files={"file": (filename, content)},
    )

    assert response.status_code == 400, response.text


def test_workflow_api_rejects_task_imports_over_5000_rows(wm_paths):
    _svc, client = _workflow_import_client(wm_paths)
    content = _task_import_workbook_bytes(
        [[f"task:{index}", "v1", "repo"] for index in range(5001)]
    )

    response = client.post(
        "/workflows/page-report/tasks/import/preview",
        headers={"X-Agent-Bridge-User": "root"},
        files={"file": ("tasks.xlsx", content)},
    )

    assert response.status_code == 400, response.text
    assert "5000" in response.text


def test_workflow_api_rejects_non_admin_task_import_endpoints(wm_paths):
    _svc, client = _workflow_import_client(wm_paths)
    headers = {"X-Agent-Bridge-User": "alice"}

    template_response = client.get("/workflows/page-report/tasks/import/template", headers=headers)
    preview_response = client.post(
        "/workflows/page-report/tasks/import/preview",
        headers=headers,
        files={"file": ("tasks.xlsx", _task_import_workbook_bytes([["task:new", "v1", "repo"]]))},
    )
    confirm_response = client.post(
        "/workflows/page-report/tasks/import/confirm",
        headers=headers,
        json={"import_id": "not-used"},
    )

    assert template_response.status_code == 403
    assert preview_response.status_code == 403
    assert confirm_response.status_code == 403


def test_workflow_api_rejects_second_task_import_confirmation(wm_paths):
    _svc, client = _workflow_import_client(wm_paths)
    headers = {"X-Agent-Bridge-User": "root"}
    preview = client.post(
        "/workflows/page-report/tasks/import/preview",
        headers=headers,
        files={"file": ("tasks.xlsx", _task_import_workbook_bytes([["task:new", "v1", "repo"]]))},
    ).json()

    assert client.post(
        "/workflows/page-report/tasks/import/confirm",
        headers=headers,
        json={"import_id": preview["import_id"]},
    ).status_code == 200
    second = client.post(
        "/workflows/page-report/tasks/import/confirm",
        headers=headers,
        json={"import_id": preview["import_id"]},
    )

    assert second.status_code == 400, second.text


def test_workflow_api_rejects_cross_workflow_task_import_confirmation(wm_paths):
    _svc, client = _workflow_import_client(wm_paths, ("page-report", "other-workflow"))
    headers = {"X-Agent-Bridge-User": "root"}
    preview = client.post(
        "/workflows/page-report/tasks/import/preview",
        headers=headers,
        files={"file": ("tasks.xlsx", _task_import_workbook_bytes([["task:new", "v1", "repo"]]))},
    ).json()

    response = client.post(
        "/workflows/other-workflow/tasks/import/confirm",
        headers=headers,
        json={"import_id": preview["import_id"]},
    )

    assert response.status_code == 400, response.text


def test_workflow_api_downloads_task_import_template(wm_paths):
    _svc, client = _workflow_import_client(wm_paths)

    response = client.get(
        "/workflows/page-report/tasks/import/template",
        headers={"X-Agent-Bridge-User": "root"},
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert response.headers["content-disposition"] == 'attachment; filename="workflow-task-template.xlsx"'
    assert response.content.startswith(b"PK")


def test_workflow_api_lists_runs_for_workflow(wm_paths):
    from agent_bridge.api.app import create_app
    from agent_bridge.app.service import AgentBridgeService

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


def test_workflow_api_lists_all_tasks_for_workflow_without_leasing(wm_paths):
    from agent_bridge.api.app import create_app
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    _seed_workflow(svc)
    svc.store.upsert_workflow_tasks(
        "page-report",
        [
            {"task_key": "page:a", "task_version": "v1", "type": "page", "payload": {"page": "a"}},
            {"task_key": "page:b", "task_version": "v1", "type": "page", "payload": {"page": "b"}},
        ],
    )
    svc.store.create_workflow_run(
        run_id="run_1", workflow_key="page-report", profile_key="report-plane",
        task_key=None, status="running", temp_dir="/tmp/run_1",
    )
    svc.store.lease_workflow_task("page-report", run_id="run_1", lease_seconds=7200)
    svc.store.append_workflow_run_log(
        run_id="run_1",
        workflow_key="page-report",
        task_key="page:a",
        level="info",
        stage="worker",
        message="processing page a",
        payload={"step": 1},
    )

    client = TestClient(create_app(wm_paths, {"root"}))
    response = client.get("/workflows/page-report/tasks", headers={"X-Agent-Bridge-User": "root"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert [task["task_key"] for task in body["tasks"]] == ["page:a", "page:b"]
    assert body["tasks"][0]["status"] == "running"
    assert body["tasks"][0]["lease_run_id"] == "run_1"
    assert body["tasks"][0]["payload"] == {"page": "a"}
    assert body["tasks"][1]["status"] == "pending"
    assert svc.store.get_workflow_task("page-report", "page:b")["status"] == "pending"


def test_workflow_api_clears_execution_data_without_deleting_definition(wm_paths):
    from agent_bridge.api.app import create_app
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    _seed_workflow(svc)
    svc.store.upsert_workflow_tasks(
        "page-report",
        [{"task_key": "page:a", "task_version": "v1", "type": "page", "payload": {"page": "a"}}],
    )
    svc.store.create_workflow_run(
        run_id="run_1", workflow_key="page-report", profile_key="report-plane",
        task_key="page:a", status="completed", temp_dir="/tmp/run_1",
    )
    svc.store.append_workflow_run_log(
        run_id="run_1",
        workflow_key="page-report",
        task_key="page:a",
        level="info",
        stage="worker",
        message="done",
        payload={},
    )
    svc.workflows.save_artifact(
        workflow_key="page-report",
        profile_key="report-plane",
        run_id="run_1",
        task_key="page:a",
        task_version="v1",
        title="Page A",
        path="pages/page-a.md",
        tags=["page"],
        format="markdown",
        summary="Page A",
        content="# Page A",
        metadata={},
    )

    client = TestClient(create_app(wm_paths, {"root"}))
    response = client.post("/workflows/page-report/clear", headers={"X-Agent-Bridge-User": "root"})

    assert response.status_code == 200, response.text
    assert response.json() == {
        "workflow_key": "page-report",
        "cleared": True,
        "tasks_deleted": 1,
        "runs_deleted": 1,
        "logs_deleted": 1,
        "artifacts_deleted": 1,
    }
    assert client.get("/workflows/page-report", headers={"X-Agent-Bridge-User": "root"}).status_code == 200
    assert client.get("/workflows/page-report/runs", headers={"X-Agent-Bridge-User": "root"}).json() == []
    assert client.get("/workflows/page-report/tasks", headers={"X-Agent-Bridge-User": "root"}).json() == {"tasks": []}
    artifacts = client.get(
        "/workflow-artifacts?profile_key=report-plane&workflow_key=page-report&include_history=true",
        headers={"X-Agent-Bridge-User": "root"},
    )
    assert artifacts.json() == {"items": [], "total": 0, "limit": 20, "offset": 0}


def test_workflow_api_deletes_workflow_and_cascades(wm_paths):
    from agent_bridge.api.app import create_app
    from agent_bridge.app.service import AgentBridgeService

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


def test_workflow_api_get_run_returns_single_run(wm_paths):
    from agent_bridge.api.app import create_app
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    _seed_workflow(svc)
    svc.store.create_workflow_run(
        run_id="run_1", workflow_key="page-report", profile_key="report-plane",
        task_key=None, status="completed", temp_dir="/tmp/run_1",
    )

    client = TestClient(create_app(wm_paths, {"root"}))
    response = client.get("/workflow-runs/run_1", headers={"X-Agent-Bridge-User": "root"})
    assert response.status_code == 200, response.text
    assert response.json()["run_id"] == "run_1"
    assert response.json()["status"] == "completed"


def test_workflow_api_get_run_404_for_unknown(wm_paths):
    from agent_bridge.api.app import create_app
    from agent_bridge.app.service import AgentBridgeService

    AgentBridgeService.create(wm_paths, {"root"}).store.init_schema()
    client = TestClient(create_app(wm_paths, {"root"}))
    response = client.get("/workflow-runs/run_nope", headers={"X-Agent-Bridge-User": "root"})
    assert response.status_code == 404


def test_workflow_api_returns_run_events_from_run_directory(wm_paths, tmp_path):
    # The /workflow-runs/{run_id}/events endpoint was removed — agent execution
    # events are now unified under /agent-runs (persisted in the agent_runs
    # table by AgentService). See test_agent_runs_api.py for the unified coverage.
    import json

    from agent_bridge.api.app import create_app
    from agent_bridge.app.service import AgentBridgeService

    run_dir = tmp_path / "run_1"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"kind": "agent_message", "message": "Reading workflow"}),
                json.dumps({"kind": "tool_call", "tool_name": "workflow_claim_task", "status": "started"}),
            ]
        ),
        encoding="utf-8",
    )

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    _seed_workflow(svc)
    svc.store.create_workflow_run(
        run_id="run_1", workflow_key="page-report", profile_key="report-plane",
        task_key=None, status="running", temp_dir=str(run_dir),
    )

    client = TestClient(create_app(wm_paths, {"root"}))
    # The workflow-specific events endpoint is gone (404); events are served
    # from /agent-runs?workflow_run_id= instead.
    response = client.get("/workflow-runs/run_1/events", headers={"X-Agent-Bridge-User": "root"})
    assert response.status_code == 404


def test_workflow_api_run_returns_409_when_already_running(wm_paths):
    from agent_bridge.api.app import create_app
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    _seed_workflow(svc)

    app = create_app(wm_paths, {"root"})
    # Simulate an in-flight run on the app's own scheduler.
    app.state.agent_bridge_service.workflow_scheduler._running.add("page-report")
    client = TestClient(app)

    response = client.post("/workflows/page-report/run", headers={"X-Agent-Bridge-User": "root"})
    assert response.status_code == 409


def test_workflow_api_run_triggers_and_completes(wm_paths, tmp_path):
    import time
    from agent_bridge.api.app import create_app
    from agent_bridge.app.service import AgentBridgeService
    from agent_bridge.automation.workflows.runner import FakeWorkflowRunner

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    _seed_workflow(svc)

    app = create_app(wm_paths, {"root"})
    # Swap the app's runner for an instant fake so the test does not shell out to claude.
    app.state.agent_bridge_service.workflow_scheduler._runner = FakeWorkflowRunner(status="no_executable_task")
    client = TestClient(app)

    started = client.post("/workflows/page-report/run", headers={"X-Agent-Bridge-User": "root"})
    assert started.status_code == 200, started.text
    run_id = started.json()["run_id"]

    status = "running"
    deadline = time.time() + 5
    while time.time() < deadline:
        r = client.get(f"/workflow-runs/{run_id}", headers={"X-Agent-Bridge-User": "root"})
        assert r.status_code == 200
        status = r.json()["status"]
        if status != "running":
            break
        time.sleep(0.05)
    assert status == "no_task"
