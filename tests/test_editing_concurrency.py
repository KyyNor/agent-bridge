from __future__ import annotations

import pytest

from agent_bridge.app.service import AgentBridgeService
from agent_bridge.core.domain import ConflictError


SCRIPT_CODE = "def main(envelope):\n    return envelope\n"
SCRIPT_SCHEMA = {
    "type": "object",
    "properties": {},
    "additionalProperties": True,
}


def test_profile_edit_tokens_reject_stale_independent_updates(wm_paths) -> None:
    service = AgentBridgeService.create(wm_paths, {"root"})
    created = service.governance.upsert_profile(
        "root",
        "safe",
        "Safe",
        "初始说明",
        "active",
        expected_edit_token="",
    )
    stale_profile_token = created["edit_token"]

    service.governance.upsert_profile(
        "root",
        "safe",
        "Safe v2",
        "另一页面已修改",
        "active",
        expected_edit_token=stale_profile_token,
    )
    with pytest.raises(ConflictError, match="其他页面更新"):
        service.governance.upsert_profile(
            "root",
            "safe",
            "旧页面",
            "旧说明",
            "disabled",
            expected_edit_token=stale_profile_token,
        )

    detail = service.governance.get_profile("root", "safe")
    stale_rules_token = detail["rules_edit_token"]
    stale_resources_token = detail["resources_edit_token"]
    service.governance.replace_profile_rules(
        "root",
        "safe",
        [{"source_type": "mcp_service", "source_key": "mysql", "effect": "allow"}],
        expected_edit_token=stale_rules_token,
    )
    with pytest.raises(ConflictError, match="其他页面更新"):
        service.governance.replace_profile_rules(
            "root",
            "safe",
            [],
            expected_edit_token=stale_rules_token,
        )

    # 规则和资源是两个独立编辑域：保存规则不应让尚未变化的资源令牌失效。
    service.governance.replace_profile_resource_rules(
        "root",
        "safe",
        [],
        expected_edit_token=stale_resources_token,
    )


def test_script_and_mcp_service_reject_stale_updates(wm_paths) -> None:
    service = AgentBridgeService.create(wm_paths, {"root"})
    script = service.scripts.upsert_script(
        actor="root",
        script_key="concurrency-test",
        name="并发测试",
        description="",
        language="python",
        code=SCRIPT_CODE,
        input_schema=SCRIPT_SCHEMA,
        output_schema=None,
        status="active",
        owner_type="system",
        owner_key="",
        expected_edit_token="",
    )
    stale_script_token = script["edit_token"]
    service.scripts.upsert_script(
        actor="root",
        script_key="concurrency-test",
        name="新页面保存",
        description="",
        language="python",
        code=SCRIPT_CODE,
        input_schema=SCRIPT_SCHEMA,
        output_schema=None,
        status="active",
        owner_type="system",
        owner_key="",
        expected_edit_token=stale_script_token,
    )
    with pytest.raises(ConflictError, match="其他页面更新"):
        service.scripts.upsert_script(
            actor="root",
            script_key="concurrency-test",
            name="旧页面覆盖",
            description="",
            language="python",
            code=SCRIPT_CODE,
            input_schema=SCRIPT_SCHEMA,
            output_schema=None,
            status="active",
            owner_type="system",
            owner_key="",
            expected_edit_token=stale_script_token,
        )

    registered = service.capabilities.register_service(
        "root",
        "mysql",
        "MySQL",
        "https://example.test/mcp",
        {"Authorization": "secret"},
        "",
        [],
        expected_edit_token="",
    )
    stale_service_token = registered["edit_token"]
    service.capabilities.register_service(
        "root",
        "mysql",
        "MySQL v2",
        "https://example.test/mcp",
        None,
        "",
        [],
        expected_edit_token=stale_service_token,
    )
    with pytest.raises(ConflictError, match="其他页面更新"):
        service.capabilities.register_service(
            "root",
            "mysql",
            "旧页面",
            "https://example.test/mcp",
            None,
            "",
            [],
            expected_edit_token=stale_service_token,
        )


def test_knowledge_configuration_rejects_stale_updates(wm_paths) -> None:
    service = AgentBridgeService.create(wm_paths, {"root"})

    category = service.upsert_category(
        "root",
        category_key="backend",
        name="后端",
        description="",
        expected_edit_token="",
    )
    stale_category_token = category["edit_token"]
    service.upsert_category(
        "root",
        category_key="backend",
        name="后端服务",
        description="",
        expected_edit_token=stale_category_token,
    )
    with pytest.raises(ConflictError, match="其他页面更新"):
        service.upsert_category(
            "root",
            category_key="backend",
            name="旧分类",
            description="",
            expected_edit_token=stale_category_token,
        )

    repository = service.codegraph.upsert_repository(
        actor="root",
        repo_key="agent-bridge",
        name="Agent Bridge",
        git_url="https://example.test/agent-bridge.git",
        branch="main",
        auth_ref="token",
        description="",
        tags=[],
        category_key="backend",
        sync_interval_minutes=60,
        auto_understand=False,
        status="active",
        expected_edit_token="",
    )
    stale_repository_token = repository["edit_token"]
    service.codegraph.upsert_repository(
        actor="root",
        repo_key="agent-bridge",
        name="Agent Bridge v2",
        git_url="https://example.test/agent-bridge.git",
        branch="main",
        auth_ref="",
        description="",
        tags=[],
        category_key="backend",
        sync_interval_minutes=60,
        auto_understand=False,
        status="active",
        expected_edit_token=stale_repository_token,
    )
    with pytest.raises(ConflictError, match="其他页面更新"):
        service.codegraph.upsert_repository(
            actor="root",
            repo_key="agent-bridge",
            name="旧仓库名",
            git_url="https://example.test/agent-bridge.git",
            branch="main",
            auth_ref="",
            description="",
            tags=[],
            category_key="backend",
            sync_interval_minutes=60,
            auto_understand=False,
            status="active",
            expected_edit_token=stale_repository_token,
        )

    kb = service.store.create_kb("docs", "Docs", "", "root")
    stale_kb_token = service.list_kbs("root")[0]["edit_token"]
    service.update_kb_defaults(
        "root",
        kb["slug"],
        default_backend_slug="primary",
        expected_edit_token=stale_kb_token,
    )
    with pytest.raises(ConflictError, match="其他页面更新"):
        service.update_kb_defaults(
            "root",
            kb["slug"],
            default_backend_slug="legacy",
            expected_edit_token=stale_kb_token,
        )
