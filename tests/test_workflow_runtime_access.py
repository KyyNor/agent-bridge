from __future__ import annotations

import asyncio

import pytest

from agent_bridge.app.service import AgentBridgeService
from agent_bridge.core.domain import AccessDenied


class _FakeMcpClient:
    async def call_tool(self, endpoint_url, headers, tool_name, arguments, timeout=150.0):
        return {"is_error": False, "content": [], "structured": {"ok": True}}


def _service_with_group_workflow(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    service.store.init_schema()
    service.access.bootstrap_admin_memberships()
    service.access.upsert_group(actor="root", group_key="team-a", name="A 组")
    service.access.upsert_group(actor="root", group_key="team-b", name="B 组")
    service.access.set_user_group(actor="root", user_id="alice", group_key="team-a")
    service.access.set_user_group(actor="root", user_id="bob", group_key="team-b")
    service.governance.upsert_profile(
        actor="alice",
        profile_key="team-a-profile",
        name="A 组能力平面",
        description="",
        status="active",
    )
    service.workflows.upsert_definition(
        actor="alice",
        workflow_key="team-a-workflow",
        name="A 组工作流",
        description="",
        profile_key="team-a-profile",
        definition={"nodes": [], "edges": []},
        status="active",
    )
    run = service.store.create_workflow_run(
        run_id="team-a-run",
        workflow_key="team-a-workflow",
        profile_key="team-a-profile",
        task_key=None,
        status="running",
        temp_dir="/tmp/team-a-run",
    )
    return service, run


def test_workflow_helpers_reject_forged_cross_group_context_and_accept_capability(wm_paths) -> None:
    service, run = _service_with_group_workflow(wm_paths)

    with pytest.raises(AccessDenied, match="其他小组"):
        service.workflows.set_tasks_for_agent(
            actor="bob",
            profile_key="team-a-profile",
            workflow_key="team-a-workflow",
            run_id="team-a-run",
            tasks=[{"task_key": "forged", "payload": {}}],
        )

    capability = service.workflows.issue_runtime_capability(run=run, initiated_by="root")
    with pytest.raises(AccessDenied, match="上下文不匹配"):
        service.workflows.require_runtime_capability(
            capability.token,
            workflow_key="team-a-workflow",
            run_id="forged-run",
            profile_key="team-a-profile",
        )

    with service.workflows.bind_runtime_capability(capability):
        result = service.workflows.set_tasks_for_agent(
            actor=capability.actor,
            profile_key="team-a-profile",
            workflow_key="team-a-workflow",
            run_id="team-a-run",
            tasks=[{"task_key": "trusted", "payload": {}}],
        )

    assert result["created"] == 1
    assert service.store.get_workflow_task("team-a-workflow", "forged") is None
    assert service.store.get_workflow_task("team-a-workflow", "trusted") is not None


def test_runtime_actor_loses_admin_bypass_and_audit_uses_workflow_group(wm_paths) -> None:
    service, run = _service_with_group_workflow(wm_paths)
    service.capabilities.mcp_client = _FakeMcpClient()
    service.capabilities.register_service(
        "bob",
        "team-b-mcp",
        "B 组 MCP",
        "https://mcp.example.test/mcp",
        {},
        "",
        [],
        visibility="shared",
    )
    service.store.upsert_mcp_tool(
        service_key="team-b-mcp",
        tool_name="search",
        display_name="search",
        description="",
        input_schema={"type": "object"},
        tool_type="search",
        tags=[],
        examples=[],
    )
    service.store.replace_profile_source_rules(
        "team-a-profile",
        [{"source_type": "mcp_service", "source_key": "team-b-mcp", "effect": "allow"}],
    )
    capability = service.workflows.issue_runtime_capability(run=run, initiated_by="root")

    with service.workflows.bind_runtime_capability(capability):
        assert asyncio.run(
            service.capabilities.execute(
                capability.actor,
                "team-b-mcp",
                "search",
                {},
                profile_key="team-a-profile",
            )
        )["success"]
        current = service.store.get_mcp_service("team-b-mcp")
        service.store.update_mcp_service(
            "team-b-mcp",
            name=current["name"],
            endpoint_url=current["endpoint_url"],
            headers={},
            description=current["description"],
            tags=[],
            visibility="group",
        )
        with pytest.raises(AccessDenied, match="其他小组"):
            asyncio.run(
                service.capabilities.execute(
                    capability.actor,
                    "team-b-mcp",
                    "search",
                    {},
                    profile_key="team-a-profile",
                )
            )

    logs = service.governance.list_logs(actor="root", source_key="team-b-mcp")
    assert [item["status"] for item in logs] == ["blocked", "success"]
    assert {item["owner_group_key"] for item in logs} == {"team-a"}
