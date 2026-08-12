from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from agent_bridge.api.app import create_app
from agent_bridge.app.service import AgentBridgeService
from agent_bridge.automation.workflows.definition import WorkflowGraph
from agent_bridge.automation.workflows.models import WorkflowType
from agent_bridge.automation.workflows.validation import validate_graph


WORKFLOW_FILE = Path(__file__).parents[1] / "examples/workflows/fine-report-analysis/workflow.json"


def test_fine_report_workflow_uses_the_current_structured_dag_contract():
    envelope = json.loads(WORKFLOW_FILE.read_text(encoding="utf-8"))
    assert envelope["format"] == "agent-bridge.workflow"
    assert envelope["format_version"] == 1
    workflow = envelope["workflow"]
    graph = WorkflowGraph.model_validate(workflow["definition"])

    validate_graph(graph, WorkflowType.summary)

    nodes = {node.id: node for node in graph.nodes}
    assert set(nodes) == {
        "get-task-initial",
        "seed-tasks",
        "get-task-retry",
        "visit-stats",
        "content-analysis",
        "lineage-trace",
        "report-output",
        "html-output",
    }
    assert nodes["get-task-initial"].config.on_empty == "continue"
    assert nodes["get-task-retry"].config.on_empty == "terminate"
    assert nodes["seed-tasks"].config.script_key == "seed_fine_report_tasks"
    assert nodes["visit-stats"].config.script_key == "query_visit_stats"
    assert nodes["content-analysis"].config.result_mode == "json"
    assert nodes["lineage-trace"].config.result_mode == "json"
    assert nodes["report-output"].type == "output"
    assert nodes["report-output"].config.system_role == "summary_markdown"
    assert nodes["html-output"].config.system_role == "summary_html"


def test_fine_report_workflow_import_reports_missing_script_reasons(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    service.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    client = TestClient(create_app(wm_paths, {"root"}))

    response = client.post(
        "/api/v1/workflows/import/preview",
        headers={"X-Agent-Bridge-User": "root"},
        files={
            "file": (
                "workflow.json",
                WORKFLOW_FILE.read_bytes(),
                "application/json",
            )
        },
    )

    assert response.status_code == 400
    body = response.json()
    assert body["detail"] == "工作流定义校验失败"
    assert {
        (issue["id"], issue["field"], issue["code"], issue["message"])
        for issue in body["errors"]
    } == {
        ("seed-tasks", "config.script_key", "missing_script", "脚本不存在或未启用: seed_fine_report_tasks"),
        ("visit-stats", "config.script_key", "missing_script", "脚本不存在或未启用: query_visit_stats"),
    }
