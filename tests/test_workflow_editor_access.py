"""工作流编辑器对本组普通用户的开放范围。

编辑器首屏需要脚本、技能、能力平面和后端目录；这些读取只依赖登录身份，
写入仍由工作流归属组决定。回归点：编辑器曾依赖管理员专用的
``/agent-runtime/config``，导致普通用户点「编辑工作流」直接 403。
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _group_scoped_client(wm_paths):
    from agent_bridge.api.app import create_app
    from agent_bridge.app.service import AgentBridgeService

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
        status="active",
        definition={"nodes": [], "edges": []},
    )
    return service, TestClient(create_app(wm_paths, {"root"}))


def test_group_member_reads_editor_resources_without_admin(wm_paths) -> None:
    _, client = _group_scoped_client(wm_paths)
    headers = {"X-Agent-Bridge-User": "alice"}

    catalog = client.get("/api/v1/agent-runtime/backends", headers=headers)
    assert catalog.status_code == 200, catalog.text
    payload = catalog.json()
    assert payload["default_backend"]
    backends = payload["available_backends"]
    assert backends and all(item["slug"] for item in backends)
    # 目录只下发节点选择所需字段，不含各后端的命令与模型配置。
    assert all("command" not in item and "model" not in item for item in backends)

    editor_requests = [
        client.get("/api/v1/workflows", headers=headers),
        client.get("/api/v1/workflows/team-a-workflow", headers=headers),
        client.get("/api/v1/capability-profiles", headers=headers),
        client.get("/api/v1/scripts", headers=headers),
        client.get("/api/v1/skills", headers=headers),
    ]
    assert [response.status_code for response in editor_requests] == [200] * len(editor_requests)


def test_group_member_edits_own_group_workflow(wm_paths) -> None:
    _, client = _group_scoped_client(wm_paths)
    headers = {"X-Agent-Bridge-User": "alice"}

    saved = client.post(
        "/api/v1/workflows",
        headers=headers,
        json={
            "workflow_key": "team-a-workflow",
            "name": "A 组工作流（改）",
            "description": "",
            "profile_key": "team-a-profile",
            "status": "active",
            "workflow_type": "operation",
            "definition": {"nodes": [], "edges": []},
        },
    )
    assert saved.status_code == 200, saved.text

    reloaded = client.get("/api/v1/workflows/team-a-workflow", headers=headers)
    assert reloaded.status_code == 200, reloaded.text
    assert reloaded.json()["name"] == "A 组工作流（改）"


def test_admin_config_and_cross_group_write_stay_forbidden(wm_paths) -> None:
    _, client = _group_scoped_client(wm_paths)

    admin_config = client.get(
        "/api/v1/agent-runtime/config", headers={"X-Agent-Bridge-User": "alice"}
    )
    assert admin_config.status_code == 403, admin_config.text
    assert admin_config.json()["detail"] == "global admin permission required"

    cross_group = client.post(
        "/api/v1/workflows",
        headers={"X-Agent-Bridge-User": "bob"},
        json={
            "workflow_key": "team-a-workflow",
            "name": "越权改名",
            "description": "",
            "profile_key": "team-a-profile",
            "status": "active",
            "workflow_type": "operation",
            "definition": {"nodes": [], "edges": []},
        },
    )
    assert cross_group.status_code == 403, cross_group.text
