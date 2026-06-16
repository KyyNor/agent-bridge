from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _create_service_with_workflow(wm_paths):
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
        schedule={"enabled": True, "start_time": "22:00", "stop_time": "07:00"},
        status="active",
    )
    return svc


def test_normal_mcp_profile_sees_artifacts_search_but_not_workflow_task_tools(wm_paths):
    from agent_bridge.capabilities.mcp_server import create_mcp_server
    from agent_bridge.knowledge.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")

    mcp = create_mcp_server(svc, profile_key="report-plane", workflow_context=None)
    tools = asyncio.run(mcp.list_tools())
    names = [tool.name for tool in tools]

    assert "artifacts_search" in names
    assert "workflow_get_task" not in names
    assert "workflow_set_task" not in names
    assert "workflow_run_log" not in names


def test_workflow_mcp_context_sees_workflow_task_tools(wm_paths):
    from agent_bridge.capabilities.mcp_server import create_mcp_server

    svc = _create_service_with_workflow(wm_paths)

    mcp = create_mcp_server(
        svc,
        profile_key="report-plane",
        workflow_context={"workflow": True, "workflow_key": "page-report", "run_id": "run_1"},
    )
    tools = asyncio.run(mcp.list_tools())
    names = [tool.name for tool in tools]

    assert "artifacts_search" in names
    assert "workflow_get_task" in names
    assert "workflow_set_task" in names
    assert "workflow_run_log" in names


def test_workflow_mcp_context_requires_workflow_key_and_run_id(wm_paths):
    from agent_bridge.capabilities.mcp_server import create_mcp_server

    svc = _create_service_with_workflow(wm_paths)

    mcp = create_mcp_server(
        svc,
        profile_key="report-plane",
        workflow_context={"workflow": True, "workflow_key": "page-report"},
    )
    tools = asyncio.run(mcp.list_tools())
    names = [tool.name for tool in tools]

    assert "artifacts_search" in names
    assert "workflow_get_task" not in names
    assert "workflow_set_task" not in names
    assert "workflow_run_log" not in names


def test_workflow_mcp_set_get_and_run_log(wm_paths):
    from agent_bridge.capabilities.mcp_server import create_mcp_server

    svc = _create_service_with_workflow(wm_paths)

    mcp = create_mcp_server(
        svc,
        profile_key="report-plane",
        workflow_context={"workflow": True, "workflow_key": "page-report", "run_id": "run_1"},
    )
    _, set_result = asyncio.run(
        mcp.call_tool(
            "workflow_set_task",
            {"tasks": [{"task_key": "page:a", "payload": {"page": "a"}}]},
        )
    )
    assert set_result["created"] == 1

    _, get_result = asyncio.run(mcp.call_tool("workflow_get_task", {}))
    assert get_result["task"]["task_key"] == "page:a"
    assert get_result["task"]["lease_run_id"] == "run_1"

    _, log_result = asyncio.run(
        mcp.call_tool(
            "workflow_run_log",
            {"level": "info", "stage": "lease", "message": "leased task", "task_key": "page:a"},
        )
    )
    assert log_result == {"ok": True}
    logs = svc.workflows.list_run_logs("root", "run_1")
    assert logs[0]["message"] == "leased task"


def test_artifacts_search_tool_returns_profile_artifacts(wm_paths):
    from agent_bridge.capabilities.mcp_server import create_mcp_server

    svc = _create_service_with_workflow(wm_paths)
    svc.workflows.save_artifact(
        workflow_key="page-report",
        profile_key="report-plane",
        run_id="run_1",
        task_key="page:a",
        title="Page A",
        path="reports/page-a/index.md",
        tags=["finance"],
        format="markdown",
        summary="Finance page",
        content="# Page A\nfinance_orders",
        metadata={},
    )

    mcp = create_mcp_server(svc, profile_key="report-plane", workflow_context=None)
    _, result = asyncio.run(mcp.call_tool("artifacts_search", {"query": "finance_orders", "tags": ["finance"]}))
    assert result["items"][0]["title"] == "Page A"


def test_mcp_route_exposes_workflow_tools_only_with_complete_workflow_headers(wm_paths):
    from agent_bridge.capabilities.mcp_server import setup_mcp_route

    svc = _create_service_with_workflow(wm_paths)
    app = FastAPI()
    setup_mcp_route(app, svc)
    client = TestClient(app)

    def list_tools(headers: dict[str, str]) -> list[str]:
        response = client.post(
            "/mcp",
            headers={**headers, "Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        assert response.status_code == 200
        payload = response.json()
        return [tool["name"] for tool in payload["result"]["tools"]]

    base_headers = {"X-Agent-Bridge-MetaMCP-Profile": "report-plane"}
    incomplete_names = list_tools({**base_headers, "X-Agent-Bridge-Workflow": "true"})
    complete_names = list_tools(
        {
            **base_headers,
            "X-Agent-Bridge-Workflow": "true",
            "X-Agent-Bridge-Workflow-Key": "page-report",
            "X-Agent-Bridge-Workflow-Run-Id": "run_1",
        }
    )

    assert "artifacts_search" in incomplete_names
    assert "workflow_get_task" not in incomplete_names
    assert "workflow_get_task" in complete_names
