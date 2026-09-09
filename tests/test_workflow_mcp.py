from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest


def _create_service_with_workflow(wm_paths):
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
        status="active",
    )
    return svc


def _create_run(svc, *, run_id: str = "run_1", workflow_key: str = "page-report", profile_key: str = "report-plane"):
    return svc.store.create_workflow_run(
        run_id=run_id,
        workflow_key=workflow_key,
        profile_key=profile_key,
        task_key=None,
        status="running",
        temp_dir="/tmp/workflow-run",
    )


def test_normal_mcp_profile_sees_artifacts_search_but_not_workflow_task_tools(wm_paths):
    from agent_bridge.capability_hub.gateway.metamcp import create_mcp_server
    from agent_bridge.app.service import AgentBridgeService

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
    from agent_bridge.capability_hub.gateway.metamcp import create_mcp_server

    svc = _create_service_with_workflow(wm_paths)

    mcp = create_mcp_server(
        svc,
        profile_key="report-plane",
        workflow_context={"workflow": True, "workflow_key": "page-report", "run_id": "run_1"},
    )
    tools = asyncio.run(mcp.list_tools())
    names = [tool.name for tool in tools]
    tools_by_name = {tool.name: tool for tool in tools}

    assert "artifacts_search" in names
    assert "workflow_get_task" in names
    assert "workflow_set_task" in names
    assert "workflow_run_log" in names
    assert tools_by_name["artifacts_search"].description == (
        "搜索当前 profile 的工作流产物。需要全文时，将结果的完整 path 作为 path 重新调用。"
    )
    artifact_schema = tools_by_name["artifacts_search"].inputSchema
    assert set(artifact_schema["properties"]) == {"query", "path", "limit"}
    assert artifact_schema["properties"]["path"]["description"] == "产物路径前缀；传入完整路径时返回正文。"
    assert tools_by_name["workflow_get_task"].description == "领取当前工作流运行中的一个待处理任务。"
    assert tools_by_name["workflow_set_task"].description == "创建或刷新当前工作流的待处理任务。"
    assert tools_by_name["workflow_set_task"].inputSchema["properties"]["tasks"]["description"] == "要写入工作流队列的任务列表。"
    assert tools_by_name["workflow_run_log"].description == "追加一条当前工作流运行日志。"
    assert tools_by_name["workflow_run_log"].inputSchema["properties"]["message"]["description"] == "日志消息正文。"


def test_workflow_mcp_context_requires_workflow_key_and_run_id(wm_paths):
    from agent_bridge.capability_hub.gateway.metamcp import create_mcp_server

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
    from agent_bridge.capability_hub.gateway.metamcp import create_mcp_server

    svc = _create_service_with_workflow(wm_paths)
    _create_run(svc)

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
    tool_logs = svc.governance.list_logs(actor="root", entrypoint="metamcp_execute")
    assert [item["tool_name"] for item in tool_logs] == [
        "workflow_run_log",
        "workflow_get_task",
        "workflow_set_task",
    ]
    assert all(item["source_key"] == "workflow" for item in tool_logs)


def test_workflow_mcp_task_type_round_trips(wm_paths):
    from agent_bridge.capability_hub.gateway.metamcp import create_mcp_server

    svc = _create_service_with_workflow(wm_paths)
    _create_run(svc)

    mcp = create_mcp_server(
        svc,
        profile_key="report-plane",
        workflow_context={"workflow": True, "workflow_key": "page-report", "run_id": "run_1"},
    )
    asyncio.run(
        mcp.call_tool(
            "workflow_set_task",
            {"tasks": [{"task_key": "page:a", "type": "page_summary", "payload": {"page": "a"}}]},
        )
    )

    _, get_result = asyncio.run(mcp.call_tool("workflow_get_task", {}))

    assert get_result["task"]["type"] == "page_summary"


def test_execute_builtin_load_skill_returns_design_workflow_prompt(wm_paths):
    svc = _create_service_with_workflow(wm_paths)

    result = asyncio.run(
        svc.capabilities.execute(
            actor="root",
            service="built-in",
            tool_name="load_skill",
            params={"skill_name": "design_workflow"},
            profile_key="report-plane",
        )
    )

    assert result["success"] is True
    assert result["result"]["skill_name"] == "design_workflow"
    assert "system.validate_workflow" in result["result"]["prompt"]
    assert "run_script" in result["result"]["prompt"]
    assert "code" in result["result"]["prompt"]


def test_workflow_mcp_rejects_mismatched_run_context(wm_paths):
    from agent_bridge.capability_hub.gateway.metamcp import create_mcp_server

    svc = _create_service_with_workflow(wm_paths)
    svc.workflows.upsert_definition(
        actor="root",
        workflow_key="other-report",
        name="Other Report",
        description="",
        profile_key="report-plane",
        status="active",
    )
    _create_run(svc, run_id="run_other", workflow_key="other-report")

    mcp = create_mcp_server(
        svc,
        profile_key="report-plane",
        workflow_context={"workflow": True, "workflow_key": "page-report", "run_id": "run_other"},
    )

    with pytest.raises(Exception, match="workflow run context mismatch"):
        asyncio.run(
            mcp.call_tool(
                "workflow_set_task",
                {"tasks": [{"task_key": "page:a", "payload": {"page": "a"}}]},
            )
        )
    with pytest.raises(Exception, match="workflow run context mismatch"):
        asyncio.run(mcp.call_tool("workflow_get_task", {}))
    with pytest.raises(Exception, match="workflow run context mismatch"):
        asyncio.run(
            mcp.call_tool(
                "workflow_run_log",
                {"level": "info", "stage": "lease", "message": "should not log"},
            )
        )

    assert svc.workflows.list_run_logs("root", "run_other") == []


def test_artifacts_search_tool_returns_profile_artifacts(wm_paths):
    from agent_bridge.capability_hub.gateway.metamcp import create_mcp_server

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
    _, result = asyncio.run(mcp.call_tool("artifacts_search", {"query": "finance_orders"}))
    item = result["items"][0]
    assert item["title"] == "Page A"
    assert "content" not in item
    for field in (
        "artifact_id", "workflow_key", "profile_key", "run_id", "task_key",
        "task_version", "is_current", "format", "tags",
    ):
        assert field not in item
    assert result["hint"] == "结果默认只含摘要；将完整 path 作为 path 重新调用可获取正文。"

    _, detail = asyncio.run(mcp.call_tool("artifacts_search", {"path": "reports/page-a/index.md"}))
    assert detail["items"][0]["content"] == "# Page A\nfinance_orders"

    logs = svc.governance.list_logs(
        actor="root",
        entrypoint="metamcp_search",
        tool_name="artifacts_search",
    )
    assert len(logs) == 2
    assert all(log["status"] == "success" for log in logs)
    assert all(log["source_type"] == "builtin" for log in logs)
    assert all(log["source_key"] == "workflow" for log in logs)
    requests = [
        json.loads(svc.governance.get_log(actor="root", log_id=log["log_id"])["request_json"])
        for log in logs
    ]
    assert {"query": "finance_orders", "path": "", "limit": 20} in requests
    assert {"query": "", "path": "reports/page-a/index.md", "limit": 20} in requests


def test_artifacts_search_logs_failure(wm_paths):
    from agent_bridge.capability_hub.gateway.metamcp import create_mcp_server

    svc = _create_service_with_workflow(wm_paths)
    mcp = create_mcp_server(svc, profile_key="report-plane", workflow_context=None)

    with pytest.raises(Exception, match="limit must be positive"):
        asyncio.run(mcp.call_tool("artifacts_search", {"query": "finance_orders", "limit": 0}))

    logs = svc.governance.list_logs(
        actor="root",
        entrypoint="metamcp_search",
        tool_name="artifacts_search",
    )
    assert len(logs) == 1
    assert logs[0]["status"] == "error"
    assert logs[0]["error_message"] == "limit must be positive"


def test_mcp_route_exposes_workflow_tools_only_with_complete_workflow_headers(wm_paths):
    from agent_bridge.access_control.identity import IdentityConfig, RequestIdentityResolver
    from agent_bridge.capability_hub.gateway.metamcp import setup_mcp_route

    svc = _create_service_with_workflow(wm_paths)
    app = FastAPI()
    setup_mcp_route(app, svc, RequestIdentityResolver(IdentityConfig()))
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

    base_headers = {
        "X-Agent-Bridge-User": "root",
        "X-Agent-Bridge-MetaMCP-Profile": "report-plane",
    }
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


def test_mcp_route_rejects_forged_cross_group_workflow_context(wm_paths):
    from agent_bridge.access_control.identity import IdentityConfig, RequestIdentityResolver
    from agent_bridge.capability_hub.gateway.metamcp import setup_mcp_route

    svc = _create_service_with_workflow(wm_paths)
    _create_run(svc)
    svc.access.upsert_group(actor="root", group_key="team-b", name="B 组")
    svc.access.set_user_group(actor="root", user_id="bob", group_key="team-b")
    app = FastAPI()
    setup_mcp_route(app, svc, RequestIdentityResolver(IdentityConfig()))
    client = TestClient(app)

    response = client.post(
        "/mcp",
        headers={
            "Accept": "application/json, text/event-stream",
            "X-Agent-Bridge-User": "bob",
            "X-Agent-Bridge-Workflow": "true",
            "X-Agent-Bridge-Workflow-Key": "page-report",
            "X-Agent-Bridge-Workflow-Run-Id": "run_1",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "workflow_set_task",
                "arguments": {"tasks": [{"task_key": "forged", "payload": {}}]},
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is True
    assert svc.store.get_workflow_task("page-report", "forged") is None


CAPABILITY_ECHO_SCRIPT = """
import os


def main(envelope):
    return {
        "capability": os.environ.get("AGENT_BRIDGE_WORKFLOW_CAPABILITY", ""),
        "actor": os.environ.get("AGENT_BRIDGE_USER", ""),
    }
"""


def test_mcp_execute_run_script_passes_capability_token_to_subprocess(wm_paths):
    from agent_bridge.access_control.identity import IdentityConfig, RequestIdentityResolver
    from agent_bridge.app.service import AgentBridgeService
    from agent_bridge.automation.workflows.runtime_capability import WORKFLOW_CAPABILITY_HEADER
    from agent_bridge.capability_hub.gateway.metamcp import setup_mcp_route

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    svc.access.bootstrap_admin_memberships()
    svc.access.upsert_group(actor="root", group_key="team-a", name="A 组")
    svc.access.set_user_group(actor="root", user_id="alice", group_key="team-a")
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
    svc.scripts.upsert_script(
        actor="alice",
        script_key="system.env_echo",
        name="Env Echo",
        description="",
        language="python",
        code=CAPABILITY_ECHO_SCRIPT,
        input_schema={"type": "object", "properties": {}, "additionalProperties": True},
        status="active",
        owner_type="system",
        owner_key="",
    )
    capability = svc.workflows.issue_runtime_capability(run=run, initiated_by="root")

    app = FastAPI()
    setup_mcp_route(app, svc, RequestIdentityResolver(IdentityConfig()))
    client = TestClient(app)
    context_headers = {
        "X-Agent-Bridge-MetaMCP-Profile": "team-a-profile",
        "X-Agent-Bridge-Workflow": "true",
        "X-Agent-Bridge-Workflow-Key": "team-a-workflow",
        "X-Agent-Bridge-Workflow-Run-Id": "team-a-run",
        "Accept": "application/json, text/event-stream",
    }

    def run_script_over_mcp(headers):
        response = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "execute",
                    "arguments": {
                        "service": "built-in",
                        "tool_name": "run_script",
                        "params": {"script_key": "system.env_echo", "script_params": {}},
                    },
                },
            },
        )
        assert response.status_code == 200
        result = response.json()["result"]
        assert result.get("isError") is not True
        structured = result.get("structuredContent")
        if structured is None:
            structured = json.loads(result["content"][0]["text"])
        return structured

    trusted = run_script_over_mcp({**context_headers, WORKFLOW_CAPABILITY_HEADER: capability.token})
    assert trusted["success"] is True
    script_output = trusted["result"]["result"]
    assert script_output["capability"] == capability.token
    assert script_output["actor"] == capability.actor

    # 后续无 capability 的请求必须读不到上一次的 token（请求级隔离）。
    plain = run_script_over_mcp({**context_headers, "X-Agent-Bridge-User": "alice"})
    assert plain["success"] is True
    assert plain["result"]["result"]["capability"] == ""
