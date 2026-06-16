from __future__ import annotations

import json


def test_parse_completed_result_reads_markdown_artifact(tmp_path):
    from agent_bridge.workflows.result_parser import parse_workflow_result

    out = tmp_path / "out"
    artifact_dir = out / "artifacts"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "report.md").write_text("# Report\n\nfinance_orders", encoding="utf-8")
    (out / "result.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "task_key": "page:a",
                "artifacts": [
                    {
                        "title": "Page A",
                        "path": "reports/page-a/index.md",
                        "tags": ["finance"],
                        "format": "markdown",
                        "file": "out/artifacts/report.md",
                        "summary": "Finance report",
                        "metadata": {"page_key": "page-a"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = parse_workflow_result(tmp_path)
    assert result.status == "completed"
    assert result.task_key == "page:a"
    assert result.artifacts[0].content == "# Report\n\nfinance_orders"


def test_parse_no_executable_task_result(tmp_path):
    from agent_bridge.workflows.result_parser import parse_workflow_result

    out = tmp_path / "out"
    out.mkdir()
    (out / "result.json").write_text(
        json.dumps({"status": "no_executable_task", "reason": "empty"}),
        encoding="utf-8",
    )

    result = parse_workflow_result(tmp_path)
    assert result.status == "no_executable_task"
    assert result.reason == "empty"
    assert result.artifacts == []


def test_parse_rejects_artifact_path_outside_run_dir(tmp_path):
    from agent_bridge.core.domain import ValidationError
    from agent_bridge.workflows.result_parser import parse_workflow_result

    out = tmp_path / "out"
    out.mkdir()
    (out / "result.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "task_key": "page:a",
                "artifacts": [
                    {
                        "title": "Bad",
                        "path": "reports/bad.md",
                        "tags": [],
                        "format": "markdown",
                        "file": "../outside.md",
                        "summary": "",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    try:
        parse_workflow_result(tmp_path)
    except ValidationError as exc:
        assert "artifact file escapes run directory" in exc.message
    else:
        raise AssertionError("outside path should fail")


def test_ingest_parsed_result_saves_artifacts_and_completes_task(wm_paths, tmp_path):
    from agent_bridge.knowledge.service import AgentBridgeService
    from agent_bridge.workflows.result_parser import parse_workflow_result

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
    svc.store.upsert_workflow_tasks("page-report", [{"task_key": "page:a", "payload": {"page": "a"}}])
    svc.store.create_workflow_run(
        run_id="run_1",
        workflow_key="page-report",
        profile_key="report-plane",
        task_key=None,
        status="running",
        temp_dir=str(tmp_path),
    )
    assert svc.store.lease_workflow_task("page-report", run_id="run_1", lease_seconds=7200) is not None

    artifact_dir = tmp_path / "out" / "artifacts"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "report.md").write_text("# Report\n\nfinance_orders", encoding="utf-8")
    (tmp_path / "out" / "result.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "task_key": "page:a",
                "artifacts": [
                    {
                        "title": "Page A",
                        "path": "reports/page-a/index.md",
                        "tags": ["finance"],
                        "format": "markdown",
                        "file": "out/artifacts/report.md",
                        "summary": "Finance report",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = svc.workflows.ingest_parsed_result(
        workflow_key="page-report",
        profile_key="report-plane",
        run_id="run_1",
        parsed=parse_workflow_result(tmp_path),
    )

    assert result["status"] == "completed"
    assert result["artifact_count"] == 1
    assert svc.store.get_workflow_task("page-report", "page:a")["status"] == "completed"
    artifacts = svc.workflows.search_artifacts(
        actor="root",
        profile_key="report-plane",
        query="finance_orders",
        tags=["finance"],
        path=None,
        workflow_key=None,
        limit=10,
    )
    assert artifacts["items"][0]["title"] == "Page A"
