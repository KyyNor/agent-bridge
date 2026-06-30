from __future__ import annotations

import json

from agent_bridge.capability_hub.governance import CapabilityGovernanceService
from agent_bridge.core.config import AgentBridgePaths
from agent_bridge.storage.sqlite import SQLiteStore


def test_render_profile_markdown_includes_usage_resources_and_manual_notes(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.upsert_project_profile(profile_key="safe", name="Safe Profile", description="", status="active", created_by="root")
    store.create_mcp_service(
        service_key="mysql",
        name="MySQL",
        endpoint_url="https://mysql.test/mcp",
        headers={},
        description="Run SQL queries.",
        tags=[],
        created_by="root",
    )
    store.update_mcp_service_status("mysql", "enabled")
    store.replace_profile_source_rules("safe", [{"source_type": "mcp_service", "source_key": "mysql", "effect": "allow"}])
    store.upsert_code_repository(
        repo_key="agent-bridge",
        name="Agent Bridge",
        git_url="https://example.test/agent-bridge.git",
        branch="main",
        auth_ref="",
        description="",
        tags=[],
        category_key="",
        sync_interval_minutes=60,
        auto_understand=False,
        status="active",
    )
    store.create_kb("frontend-docs", "Frontend Docs", "", "root")
    store.replace_profile_resource_rules(
        "safe",
        [
            {"resource_type": "code_repo", "resource_key": "agent-bridge"},
            {"resource_type": "wiki_kb", "resource_key": "frontend-docs"},
        ],
    )
    governance = CapabilityGovernanceService(store=store, admins={"root"})
    governance.update_profile_manual_notes("root", "safe", "## Manual Notes\nUse read-only queries only.")

    rendered = governance.render_profile_markdown("root", "safe")

    assert "# Agent Bridge Profile：Safe Profile" in rendered["markdown"]
    assert "search" in rendered["markdown"]
    assert "execute" in rendered["markdown"]
    assert "- MySQL (`mysql`)：Run SQL queries." in rendered["markdown"]
    assert "- Agent Bridge (`agent-bridge`)" in rendered["markdown"]
    assert "- Frontend Docs (`frontend-docs`)" in rendered["markdown"]
    assert "Use read-only queries only." in rendered["markdown"]
    assert "https://mysql.test/mcp" not in rendered["markdown"]
    assert "pin_mysql" not in rendered["markdown"]

    cache = store.get_profile_doc_cache("safe")
    assert cache is not None
    assert cache["manual_notes"] == "## Manual Notes\nUse read-only queries only."
    assert cache["last_rendered_markdown"] == rendered["markdown"]
    assert cache["rendered_hash"] == rendered["rendered_hash"]
    assert json.loads(cache["auto_summary_json"])["services"] == [
        {"service_key": "mysql", "name": "MySQL", "description": "Run SQL queries."}
    ]
    assert "https://mysql.test/mcp" not in cache["auto_summary_json"]


def test_render_profile_markdown_allows_non_admin_actor(wm_paths: AgentBridgePaths) -> None:
    """render_profile_markdown 是只读渲染，对非 admin 用户开放（SessionStart hook / agent 运行时路径）。"""
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.upsert_project_profile(profile_key="safe", name="Safe Profile", description="", status="active", created_by="root")
    store.create_mcp_service(
        service_key="mysql",
        name="MySQL",
        endpoint_url="https://mysql.test/mcp",
        headers={},
        description="",
        tags=[],
        created_by="root",
    )
    store.update_mcp_service_status("mysql", "enabled")
    store.replace_profile_source_rules("safe", [{"source_type": "mcp_service", "source_key": "mysql", "effect": "allow"}])
    governance = CapabilityGovernanceService(store=store, admins={"root"})  # admins 不含 alice

    # 非 admin 用户（alice）应能成功渲染，且结果与 admin（root）完全一致。
    rendered_by_user = governance.render_profile_markdown("alice", "safe")
    rendered_by_admin = governance.render_profile_markdown("root", "safe")

    assert "Safe Profile" in rendered_by_user["markdown"]
    assert rendered_by_user["markdown"] == rendered_by_admin["markdown"]
    assert rendered_by_user["rendered_hash"] == rendered_by_admin["rendered_hash"]
