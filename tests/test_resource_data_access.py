from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from agent_bridge.access_control.resources import create_scoped_resource_registry
from agent_bridge.access_control.service import AccessControlService
from agent_bridge.api.app import create_app
from agent_bridge.capability_hub.service import CapabilityService
from agent_bridge.core.domain import AccessDenied
from agent_bridge.storage.sqlite import SQLiteStore


def _configure_groups(client: TestClient) -> None:
    root = {"X-Agent-Bridge-User": "root"}
    for group_key in ("team-a", "team-b"):
        response = client.post(
            "/access/groups",
            headers=root,
            json={"group_key": group_key, "name": group_key.upper()},
        )
        assert response.status_code == 200
    for user_id, group_key in (("alice", "team-a"), ("carol", "team-a"), ("bob", "team-b")):
        response = client.put(
            "/access/memberships",
            headers=root,
            json={"user_id": user_id, "group_key": group_key},
        )
        assert response.status_code == 200


def test_knowledge_base_group_and_shared_access_is_enforced_by_backend(wm_paths) -> None:
    client = TestClient(create_app(wm_paths, admins={"root"}))
    _configure_groups(client)
    alice = {"X-Agent-Bridge-User": "alice"}
    bob = {"X-Agent-Bridge-User": "bob"}
    carol = {"X-Agent-Bridge-User": "carol"}

    assert client.post(
        "/kbs",
        headers=alice,
        json={"slug": "team-notes", "name": "组内知识", "visibility": "group"},
    ).status_code == 200
    assert client.post(
        "/kbs",
        headers=alice,
        json={"slug": "shared-notes", "name": "共享知识", "visibility": "shared"},
    ).status_code == 200

    assert [item["slug"] for item in client.get("/kbs", headers=bob).json()] == ["shared-notes"]
    assert client.get("/kbs/team-notes/folders", headers=bob).status_code == 403
    assert client.get("/kbs/shared-notes/folders", headers=bob).status_code == 200
    assert client.post(
        "/kbs/shared-notes/folders",
        headers=bob,
        json={"name": "不能跨组新建"},
    ).status_code == 403
    assert client.post(
        "/kbs/team-notes/folders",
        headers=carol,
        json={"name": "同组可新建"},
    ).status_code == 200


def test_capability_and_code_repository_lists_intersect_with_group_access(wm_paths) -> None:
    client = TestClient(create_app(wm_paths, admins={"root"}))
    _configure_groups(client)
    alice = {"X-Agent-Bridge-User": "alice"}
    bob = {"X-Agent-Bridge-User": "bob"}
    carol = {"X-Agent-Bridge-User": "carol"}

    mcp_base = {
        "name": "内部 MCP",
        "endpoint_url": "https://mcp.example.test/mcp",
        "description": "组内能力",
        "tags": ["internal"],
    }
    assert client.post(
        "/capabilities/mcp-services",
        headers=alice,
        json={**mcp_base, "service_key": "team-mcp", "visibility": "group"},
    ).status_code == 200
    assert client.post(
        "/capabilities/mcp-services",
        headers=alice,
        json={**mcp_base, "service_key": "shared-mcp", "visibility": "shared"},
    ).status_code == 200

    assert [item["service_key"] for item in client.get(
        "/capabilities/mcp-services", headers=bob
    ).json()] == ["shared-mcp"]
    assert client.get("/capabilities/mcp-services/team-mcp", headers=bob).status_code == 403
    assert client.post(
        "/capabilities/mcp-services",
        headers=bob,
        json={**mcp_base, "service_key": "shared-mcp", "visibility": "shared"},
    ).status_code == 403
    assert client.post(
        "/capabilities/mcp-services",
        headers=carol,
        json={**mcp_base, "service_key": "shared-mcp", "name": "同组更新"},
    ).status_code == 200

    repo_base = {
        "name": "代码参考",
        "git_url": "https://git.example.test/team/repo.git",
        "branch": "main",
    }
    assert client.post(
        "/code-repo/repositories",
        headers=alice,
        json={**repo_base, "repo_key": "team-code", "visibility": "group"},
    ).status_code == 200
    assert client.post(
        "/code-repo/repositories",
        headers=alice,
        json={**repo_base, "repo_key": "shared-code", "visibility": "shared"},
    ).status_code == 200
    assert [item["repo_key"] for item in client.get(
        "/code-repo/repositories", headers=bob
    ).json()] == ["shared-code"]
    assert client.get("/code-repo/repositories/team-code", headers=bob).status_code == 403
    assert client.post(
        "/code-repo/repositories",
        headers=bob,
        json={**repo_base, "repo_key": "shared-code", "visibility": "shared"},
    ).status_code == 403


class _FakeMcpClient:
    async def call_tool(self, endpoint_url, headers, tool_name, arguments, timeout=150.0):
        return {"is_error": False, "content": [], "structured": {"actor_can_use": True}}


def test_mcp_execution_checks_data_scope_before_transport(wm_paths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    access = AccessControlService(
        store.access_control,
        {"root"},
        create_scoped_resource_registry(store),
    )
    access.bootstrap_admin_memberships()
    access.upsert_group(actor="root", group_key="team-a", name="A 组")
    access.upsert_group(actor="root", group_key="team-b", name="B 组")
    access.set_user_group(actor="root", user_id="alice", group_key="team-a")
    access.set_user_group(actor="root", user_id="carol", group_key="team-a")
    access.set_user_group(actor="root", user_id="bob", group_key="team-b")
    service = CapabilityService(
        store=store,
        admins={"root"},
        access=access,
        mcp_client=_FakeMcpClient(),
    )
    for service_key, visibility in (("team-mcp", "group"), ("shared-mcp", "shared")):
        service.register_service(
            "alice",
            service_key,
            service_key,
            "https://mcp.example.test/mcp",
            {},
            "",
            [],
            visibility=visibility,
        )
        store.upsert_mcp_tool(
            service_key=service_key,
            tool_name="search",
            display_name="search",
            description="",
            input_schema={"type": "object"},
            tool_type="search",
            tags=[],
            examples=[],
        )

    with pytest.raises(AccessDenied, match="其他小组"):
        asyncio.run(service.execute("bob", "team-mcp", "search", {}))
    assert asyncio.run(service.execute("bob", "shared-mcp", "search", {}))["success"]
    assert asyncio.run(service.execute("carol", "team-mcp", "search", {}))["success"]


def test_profiles_ledgers_and_memory_follow_group_scope(wm_paths) -> None:
    client = TestClient(create_app(wm_paths, admins={"root"}))
    _configure_groups(client)
    alice = {"X-Agent-Bridge-User": "alice"}
    bob = {"X-Agent-Bridge-User": "bob"}
    carol = {"X-Agent-Bridge-User": "carol"}

    profile_payload = {
        "profile_key": "team-a-profile",
        "name": "A 组能力平面",
        "description": "",
        "status": "active",
    }
    assert client.post("/capability-profiles", headers=alice, json=profile_payload).status_code == 200
    assert [item["profile_key"] for item in client.get("/capability-profiles", headers=carol).json()] == [
        "team-a-profile"
    ]
    assert client.get("/capability-profiles", headers=bob).json() == []
    assert client.get("/capability-profiles/team-a-profile", headers=bob).status_code == 403

    fields = [
        {
            "field_key": "name",
            "name": "名称",
            "field_type": "text",
            "query_modes": ["contains"],
        }
    ]
    for ledger_key, visibility in (("team-ledger", "group"), ("shared-ledger", "shared")):
        assert client.post(
            "/business-ledgers",
            headers=alice,
            json={
                "ledger_key": ledger_key,
                "name": ledger_key,
                "fields": fields,
                "visibility": visibility,
            },
        ).status_code == 200
    assert client.post(
        "/business-ledgers/shared-ledger/records",
        headers=alice,
        json={"values": {"name": "可共享记录"}},
    ).status_code == 200
    assert [item["ledger_key"] for item in client.get("/business-ledgers", headers=bob).json()] == [
        "shared-ledger"
    ]
    assert client.get("/business-ledgers/team-ledger", headers=bob).status_code == 403
    assert client.post(
        "/business-ledgers/shared-ledger/records/query",
        headers=bob,
        json={"filters": {}, "limit": 10, "offset": 0},
    ).status_code == 200
    assert client.post(
        "/business-ledgers/shared-ledger/records",
        headers=bob,
        json={"values": {"name": "禁止跨组写入"}},
    ).status_code == 403
    assert client.post(
        "/capability-profiles",
        headers=bob,
        json={
            "profile_key": "team-b-profile",
            "name": "B 组能力平面",
            "description": "",
            "status": "active",
        },
    ).status_code == 200
    assert client.put(
        "/capability-profiles/team-b-profile/resources",
        headers=bob,
        json={
            "resources": [
                {"resource_type": "business_ledger", "resource_key": "shared-ledger"}
            ]
        },
    ).status_code == 200
    assert client.put(
        "/capability-profiles/team-b-profile/resources",
        headers=bob,
        json={
            "resources": [
                {"resource_type": "business_ledger", "resource_key": "team-ledger"}
            ]
        },
    ).status_code == 403

    assert client.post(
        "/memory/blocks",
        headers=alice,
        json={"block_key": "team-a-memory", "name": "A 组记忆", "description": ""},
    ).status_code == 200
    assert [item["block_key"] for item in client.get("/memory/blocks", headers=carol).json()] == [
        "team-a-memory"
    ]
    assert client.get("/memory/blocks", headers=bob).json() == []
    assert client.get("/memory/blocks/team-a-memory", headers=bob).status_code == 403


def test_workflow_definition_is_group_only_and_artifact_can_be_shared(wm_paths) -> None:
    app = create_app(wm_paths, admins={"root"})
    client = TestClient(app)
    _configure_groups(client)
    alice = {"X-Agent-Bridge-User": "alice"}
    bob = {"X-Agent-Bridge-User": "bob"}

    assert client.post(
        "/capability-profiles",
        headers=alice,
        json={
            "profile_key": "team-a-workflow",
            "name": "A 组工作流能力平面",
            "description": "",
            "status": "active",
        },
    ).status_code == 200
    assert client.post(
        "/workflows",
        headers=alice,
        json={
            "workflow_key": "team-a-report",
            "name": "A 组报告",
            "description": "",
            "profile_key": "team-a-workflow",
            "definition": {"nodes": [], "edges": []},
            "status": "active",
        },
    ).status_code == 200

    assert [item["workflow_key"] for item in client.get("/workflows", headers=alice).json()] == [
        "team-a-report"
    ]
    assert client.get("/workflows", headers=bob).json() == []
    assert client.get("/workflows/team-a-report", headers=bob).status_code == 403

    artifact = app.state.agent_bridge_service.workflows.save_artifact(
        workflow_key="team-a-report",
        profile_key="team-a-workflow",
        run_id="run-team-a",
        task_key="report:one",
        title="A 组报告产物",
        path="reports/team-a.md",
        tags=["report"],
        format="markdown",
        summary="只在共享后跨组可见",
        content="# A 组报告",
        metadata={},
    )
    artifact_id = artifact["artifact_id"]

    assert client.get(f"/workflow-artifacts/{artifact_id}", headers=bob).status_code == 403
    assert client.get("/workflow-artifacts", headers=bob).json()["items"] == []
    assert client.put(
        f"/workflow-artifacts/{artifact_id}/visibility",
        headers=alice,
        json={"visibility": "shared"},
    ).status_code == 200

    shared = client.get(f"/workflow-artifacts/{artifact_id}", headers=bob)
    assert shared.status_code == 200
    assert shared.json()["visibility"] == "shared"
    assert [item["artifact_id"] for item in client.get(
        "/workflow-artifacts", headers=bob
    ).json()["items"]] == [artifact_id]
    assert client.put(
        f"/workflow-artifacts/{artifact_id}/visibility",
        headers=bob,
        json={"visibility": "group"},
    ).status_code == 403


def test_user_scripts_and_their_runs_are_group_scoped(wm_paths) -> None:
    client = TestClient(create_app(wm_paths, admins={"root"}))
    _configure_groups(client)
    alice = {"X-Agent-Bridge-User": "alice"}
    bob = {"X-Agent-Bridge-User": "bob"}
    carol = {"X-Agent-Bridge-User": "carol"}
    payload = {
        "script_key": "team-a.echo",
        "name": "A 组脚本",
        "description": "",
        "language": "python",
        "code": "def main(envelope):\n    return {'ok': True}\n",
        "input_schema": {"type": "object", "additionalProperties": True},
        "status": "active",
        "owner_type": "system",
        "owner_key": "",
    }

    assert client.post("/scripts", headers=alice, json=payload).status_code == 200
    assert "team-a.echo" in [item["script_key"] for item in client.get(
        "/scripts", headers=carol
    ).json()]
    assert "team-a.echo" not in [item["script_key"] for item in client.get(
        "/scripts", headers=bob
    ).json()]
    assert client.get("/scripts/team-a.echo", headers=bob).status_code == 403
    assert client.post(
        "/scripts/team-a.echo/test", headers=bob, json={"params": {}}
    ).status_code == 403

    executed = client.post(
        "/scripts/team-a.echo/test", headers=alice, json={"params": {}}
    )
    assert executed.status_code == 200
    run_id = executed.json()["run_id"]
    assert client.get(f"/script-runs/{run_id}", headers=carol).status_code == 200
    assert client.get(f"/script-runs/{run_id}", headers=bob).status_code == 403


def test_model_evaluation_runs_are_group_scoped(wm_paths) -> None:
    app = create_app(wm_paths, admins={"root"})
    client = TestClient(app)
    _configure_groups(client)
    alice = {"X-Agent-Bridge-User": "alice"}
    bob = {"X-Agent-Bridge-User": "bob"}
    service = app.state.agent_bridge_service

    for run_id, owner_group_key, created_by in (
        ("eval_team_a", "team-a", "alice"),
        ("eval_team_b", "team-b", "bob"),
    ):
        service.store.create_model_evaluation_run(
            run_id=run_id,
            model_name="internal-model",
            base_url="https://llm.example.test/v1",
            datasets=["gsm8k_chat_gen"],
            max_samples=10,
            sampling_mode="head",
            sample_seed=42,
            work_dir=f"/tmp/{run_id}",
            created_by=created_by,
            runtime="docker",
            owner_group_key=owner_group_key,
        )

    assert client.get("/model-evaluations/datasets", headers=alice).status_code == 200
    assert [item["run_id"] for item in client.get(
        "/model-evaluations", headers=alice
    ).json()] == ["eval_team_a"]
    assert [item["run_id"] for item in client.get(
        "/model-evaluations", headers=bob
    ).json()] == ["eval_team_b"]
    assert client.get("/model-evaluations/eval_team_a", headers=alice).status_code == 200
    assert client.get("/model-evaluations/eval_team_a", headers=bob).status_code == 403
