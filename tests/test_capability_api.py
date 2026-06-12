from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from agent_bridge.capabilities.models import FailureOwner, FailureStage, SourceType, ToolType
from agent_bridge.api.app import create_app
from agent_bridge.storage.sqlite import SQLiteStore


def _git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "--initial-branch=master"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    (path / "app.py").write_text("def hello():\n    return 'world'\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, capture_output=True)
    return path


def test_mcp_service_registration_api(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)

    created = client.post(
        "/capabilities/mcp-services",
        json={
            "service_key": "mysql",
            "name": "MySQL MCP",
            "endpoint_url": "https://mysql.example.test/mcp",
            "headers": {"Authorization": "Bearer secret"},
            "description": "Database tools",
            "tags": ["database"],
        },
        headers={"X-Agent-Bridge-User": "root"},
    )
    listed = client.get("/capabilities/mcp-services", headers={"X-Agent-Bridge-User": "alice"})

    assert created.status_code == 200
    assert created.json()["service_key"] == "mysql"
    assert listed.status_code == 200
    assert listed.json()[0]["service_key"] == "mysql"
    assert listed.json()[0]["tags"] == ["database"]
    assert listed.json()[0]["headers"] == {"Authorization": "***"}


def test_mcp_service_registration_requires_admin(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)

    response = client.post(
        "/capabilities/mcp-services",
        json={
            "service_key": "mysql",
            "name": "MySQL MCP",
            "endpoint_url": "https://mysql.example.test/mcp",
            "headers": {},
            "description": "",
            "tags": [],
        },
        headers={"X-Agent-Bridge-User": "alice"},
    )

    assert response.status_code == 403


def test_mcp_service_update_without_headers_preserves_existing_headers(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    client.post(
        "/capabilities/mcp-services",
        json={
            "service_key": "mysql",
            "name": "MySQL MCP",
            "endpoint_url": "https://mysql.example.test/mcp",
            "headers": {"Authorization": "Bearer secret"},
            "description": "Database tools",
            "tags": ["database"],
        },
        headers={"X-Agent-Bridge-User": "root"},
    )

    updated = client.post(
        "/capabilities/mcp-services",
        json={
            "service_key": "mysql",
            "name": "MySQL Reporting MCP",
            "endpoint_url": "https://mysql.example.test/mcp",
            "description": "Updated description",
            "tags": ["database", "reporting"],
        },
        headers={"X-Agent-Bridge-User": "root"},
    )
    listed = client.get("/capabilities/mcp-services", headers={"X-Agent-Bridge-User": "root"})

    assert updated.status_code == 200
    assert updated.json()["headers"] == {"Authorization": "Bearer secret"}
    assert listed.json()[0]["headers"] == {"Authorization": "***"}
    assert listed.json()[0]["tags"] == ["database", "reporting"]


def test_capability_admin_page_serves_html(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)

    response = client.get("/admin/capabilities", headers={"X-Agent-Bridge-User": "root"})

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "智能中枢" in response.text


def test_root_redirects_to_capability_admin_page(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/admin/capabilities"


def test_capability_static_assets_are_served(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)

    css = client.get("/static/capabilities/index.html")
    assert css.status_code == 200
    assert b"\xe6\x99\xba\xe8\x83\xbd\xe4\xb8\xad\xe6\x9e\xa2" in css.content


def test_capability_admin_page_is_chinese_control_console(wm_paths) -> None:
    from agent_bridge.web.pages import capability_admin_page

    html = capability_admin_page("root")
    assert "智能中枢" in html

def test_capability_static_assets_use_chinese_labels(wm_paths) -> None:
    from agent_bridge.web.pages import capability_admin_page

    html = capability_admin_page("root")
    assert "capabilit" in html

def test_capability_admin_page_uses_modal_service_form_and_no_refresh_buttons(wm_paths) -> None:
    from agent_bridge.web.pages import capability_admin_page

    html = capability_admin_page("root")
    assert 'id="app"' in html

def test_capability_admin_page_has_phase2_views_and_modals(wm_paths) -> None:
    from agent_bridge.web.pages import capability_admin_page

    html = capability_admin_page("root")
    assert 'id="app"' in html

def test_capability_static_assets_support_phase2_interactions(wm_paths) -> None:
    from agent_bridge.web.pages import capability_admin_page

    html = capability_admin_page("root")
    assert "script" in html

def test_capability_static_assets_support_query_route_state(wm_paths) -> None:
    from agent_bridge.web.pages import capability_admin_page

    html = capability_admin_page("root")
    assert "html" in html

def test_capability_admin_page_has_profile_dialog_and_tool_filters(wm_paths) -> None:
    from agent_bridge.web.pages import capability_admin_page

    html = capability_admin_page("root")
    assert "智能中枢" in html

def test_capability_static_assets_render_filterable_tool_table_and_two_column_modal(wm_paths) -> None:
    from agent_bridge.web.pages import capability_admin_page

    html = capability_admin_page("root")
    assert 'id="app"' in html

def test_mcp_service_status_and_tools_api(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    client.post(
        "/capabilities/mcp-services",
        json={
            "service_key": "mysql",
            "name": "MySQL MCP",
            "endpoint_url": "https://mysql.example.test/mcp",
        },
        headers={"X-Agent-Bridge-User": "root"},
    )

    disabled = client.post(
        "/capabilities/mcp-services/mysql/status",
        json={"status": "disabled"},
        headers={"X-Agent-Bridge-User": "root"},
    )
    tools = client.get("/capabilities/mcp-services/mysql/tools", headers={"X-Agent-Bridge-User": "root"})

    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    assert tools.status_code == 400
    assert tools.json()["detail"] == "MCP service is not enabled"


def test_mcp_tool_type_api_requires_admin_and_updates_tool(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    client.post(
        "/capabilities/mcp-services",
        json={"service_key": "mysql", "name": "MySQL MCP", "endpoint_url": "https://mysql.example.test/mcp"},
        headers={"X-Agent-Bridge-User": "root"},
    )
    store = SQLiteStore(wm_paths.db_path)
    store.upsert_mcp_tool(
        service_key="mysql",
        tool_name="query_sql",
        display_name="Query SQL",
        description="Run SQL",
        input_schema={"type": "object"},
        tool_type=ToolType.unconfigured.value,
        tags=[],
        examples=[],
    )

    denied = client.put(
        "/capabilities/mcp-services/mysql/tools/query_sql/type",
        json={"tool_type": "search"},
        headers={"X-Agent-Bridge-User": "alice"},
    )
    invalid = client.put(
        "/capabilities/mcp-services/mysql/tools/query_sql/type",
        json={"tool_type": "other"},
        headers={"X-Agent-Bridge-User": "root"},
    )
    updated = client.put(
        "/capabilities/mcp-services/mysql/tools/query_sql/type",
        json={"tool_type": "search"},
        headers={"X-Agent-Bridge-User": "root"},
    )

    assert denied.status_code == 403
    assert invalid.status_code == 400
    assert invalid.json()["detail"] == "invalid tool type"
    assert updated.status_code == 200
    assert updated.json()["tool_type"] == "search"


def test_profile_api_and_catalog_preview(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    client.post(
        "/capabilities/mcp-services",
        json={"service_key": "mysql", "name": "MySQL", "endpoint_url": "https://mysql.test/mcp"},
        headers={"X-Agent-Bridge-User": "root"},
    )
    client.post(
        "/capabilities/mcp-services",
        json={"service_key": "hive", "name": "Hive", "endpoint_url": "https://hive.test/mcp"},
        headers={"X-Agent-Bridge-User": "root"},
    )

    created = client.post(
        "/capability-profiles",
        json={"profile_key": "safe-readonly", "name": "安全只读", "description": "", "status": "active"},
        headers={"X-Agent-Bridge-User": "root"},
    )
    rules = client.put(
        "/capability-profiles/safe-readonly/rules",
        json={"rules": [{"source_type": "mcp_service", "source_key": "hive", "effect": "deny"}]},
        headers={"X-Agent-Bridge-User": "root"},
    )
    listed = client.get("/capability-profiles", headers={"X-Agent-Bridge-User": "root"})
    detail = client.get("/capability-profiles/safe-readonly", headers={"X-Agent-Bridge-User": "root"})
    catalog = client.get(
        "/capability-catalog",
        params={"profile_key": "safe-readonly"},
        headers={"X-Agent-Bridge-User": "root"},
    )

    assert created.status_code == 200
    assert created.json()["profile_key"] == "safe-readonly"
    assert rules.status_code == 200
    assert rules.json()["rules"][0]["source_key"] == "hive"
    assert listed.status_code == 200
    assert listed.json()[0]["deny_count"] == 1
    assert detail.status_code == 200
    assert detail.json()["rules"][0]["effect"] == "deny"
    assert catalog.status_code == 200
    assert [item["source_key"] for item in catalog.json()["sources"]] == []


def test_profile_resource_rules_api(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    client.post(
        "/capability-profiles",
        json={"profile_key": "safe-readonly", "name": "安全只读", "description": "", "status": "active"},
        headers={"X-Agent-Bridge-User": "root"},
    )

    saved = client.put(
        "/capability-profiles/safe-readonly/resources",
        json={"resources": [{"resource_type": "wiki_kb", "resource_key": "frontend-docs"}]},
        headers={"X-Agent-Bridge-User": "root"},
    )
    detail = client.get("/capability-profiles/safe-readonly", headers={"X-Agent-Bridge-User": "root"})

    assert saved.status_code == 200
    assert saved.json()["resource_rules"][0]["resource_key"] == "frontend-docs"
    assert detail.json()["resource_rules"][0]["resource_type"] == "wiki_kb"


def test_builtin_wiki_kbs_api_returns_status_summary(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    client.post(
        "/kbs",
        json={"slug": "frontend-docs", "name": "Frontend Docs", "description": ""},
        headers={"X-Agent-Bridge-User": "root"},
    )

    response = client.get("/builtin/wiki/kbs", headers={"X-Agent-Bridge-User": "root"})

    assert response.status_code == 200
    assert response.json()[0]["slug"] == "frontend-docs"
    assert "backend_targets" in response.json()[0]
    assert "document_count" in response.json()[0]


def test_knowledge_web_management_api_flow(tmp_path: Path, wm_paths) -> None:
    wm_paths.config_dir.mkdir(parents=True, exist_ok=True)
    wm_paths.server_config_path.write_text(
        'host = "127.0.0.1"\nport = 8765\nadmins = ["root"]\n\n[backends.mock]\nbackend_type = "mock"\n',
        encoding="utf-8",
    )
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    source = tmp_path / "Guide.md"
    source.write_text("# Guide\n\nhello web knowledge\n", encoding="utf-8")
    client.post("/admin/init", headers={"X-Agent-Bridge-User": "root"})

    created = client.post(
        "/kbs",
        json={"slug": "frontend-docs", "name": "Frontend Docs", "description": ""},
        headers={"X-Agent-Bridge-User": "root"},
    )
    uploaded = client.post(
        "/docs",
        data={"kb": "frontend-docs", "later": "true"},
        files={"file": ("Guide.md", source.read_bytes(), "text/markdown")},
        headers={"X-Agent-Bridge-User": "root"},
    )
    blocked_upload = client.post(
        "/docs",
        data={"kb": "frontend-docs", "later": "true"},
        files={"file": ("Guide.md", source.read_bytes(), "text/markdown")},
        headers={"X-Agent-Bridge-User": "alice"},
    )
    docs = client.get("/docs", params={"kb": "frontend-docs"}, headers={"X-Agent-Bridge-User": "root"})
    detail = client.get("/docs/guide", headers={"X-Agent-Bridge-User": "root"})
    status = client.get("/status", headers={"X-Agent-Bridge-User": "root"})
    summary = client.get("/builtin/wiki/kbs", headers={"X-Agent-Bridge-User": "root"})

    assert created.status_code == 200
    assert uploaded.status_code == 200
    assert uploaded.json()["slug"] == "guide"
    assert blocked_upload.status_code == 403
    assert docs.status_code == 200
    assert docs.json()[0]["slug"] == "guide"
    assert detail.status_code == 200
    assert detail.json()["kb_slugs"] == ["frontend-docs"]
    assert status.status_code == 200
    assert status.json()["jobs"][0]["status"] == "pending"
    assert summary.status_code == 200
    assert summary.json()[0]["document_count"] == 1


def test_backend_list_reports_weknora_type(wm_paths) -> None:
    wm_paths.config_dir.mkdir(parents=True, exist_ok=True)
    wm_paths.server_config_path.write_text(
        '\n'.join([
            'host = "127.0.0.1"',
            'port = 8765',
            'admins = ["root"]',
            '',
            '[backends.weknora-main]',
            'backend_type = "weknora"',
            'base_url = "http://weknora.example.test"',
            'api_key = "test-key"',
        ]),
        encoding="utf-8",
    )
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)

    response = client.get("/backends", headers={"X-Agent-Bridge-User": "root"})

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["slug"] == "weknora-main"
    assert payload[0]["backend_type"] == "weknora"
    assert payload[0]["status"] == "active"
    assert payload[0]["api_key_set"] is True
    assert "api_key" not in payload[0]


def test_backend_list_requires_admin(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)

    response = client.get("/backends", headers={"X-Agent-Bridge-User": "alice"})

    assert response.status_code == 403


def test_frontend_stats_view_uses_calls_field_from_backend() -> None:
    source = Path("frontend/capabilities/src/views/monitoring/StatsView.vue").read_text(encoding="utf-8")

    assert "callCount" in source
    assert "s.count" not in source


def test_frontend_knowledge_navigation_groups_document_code_and_config() -> None:
    source = Path("frontend/capabilities/src/App.vue").read_text(encoding="utf-8")

    assert "label: '资源管理'" not in source
    assert "key: 'knowledge', label: '文档知识'" in source
    assert "key: 'code-repos', label: '代码知识'" in source
    assert "key: 'knowledge-config', label: '知识处理配置'" in source
    assert "KnowledgeProcessingConfigView" in source
    assert "CodeRepoView" in source
    assert "view === 'knowledge-config'" in source
    assert "view === 'code-repos'" in source
    assert "BuiltinsView" not in source
    assert "view === 'builtins'" not in source
    assert source.count("label: '知识管理'") == 1


def test_frontend_knowledge_copy_uses_document_and_code_knowledge_names() -> None:
    knowledge = Path("frontend/capabilities/src/views/knowledge/KnowledgeView.vue").read_text(encoding="utf-8")
    profiles = Path("frontend/capabilities/src/views/capabilities/ProfilesView.vue").read_text(encoding="utf-8")

    assert "创建文档知识" in knowledge
    assert "暂无文档知识，点击「创建文档知识」开始" in knowledge
    assert "允许访问的文档知识" in profiles
    assert "请先在文档知识中添加" in profiles
    assert "请先在代码知识中添加" in profiles


def test_frontend_codegraph_detail_uses_single_query_panel() -> None:
    source = Path("frontend/capabilities/src/views/knowledge/CodeRepoView.vue").read_text(encoding="utf-8")

    assert "detailTab === 'overview'" in source
    assert "detailTab === 'explore'" in source
    assert "key: 'explore', label: '探索'" in source
    assert "仓库信息" in source
    assert "同步错误" in source
    assert "CodeGraph CLI 已安装" not in source
    assert "CodeGraph 状态" not in source
    assert "调用者" not in source
    assert "被调用者" not in source
    assert "key: 'files'" not in source
    assert "detailTab === 'files'" not in source
    assert "api.listRepoFiles" not in source
    assert "api.findCallers" not in source
    assert "api.findCallees" not in source
    assert "api.analyzeImpact" not in source
    assert "api.queryRepo" in source
    assert "api.exploreRepo" in source
    assert "searchInRepo('callers')" not in source


def test_frontend_knowledge_processing_config_page_has_sync_config() -> None:
    source = Path("frontend/capabilities/src/views/knowledge/KnowledgeProcessingConfigView.vue").read_text(encoding="utf-8")

    assert "定时任务管理" in source
    assert "code_sync_cron" in source
    assert "doc_sync_cron" in source
    assert "知识同步" in source
    assert "最近进度" in source
    assert "当前执行" in source


def test_tool_call_log_api_returns_full_payload(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    from agent_bridge.capabilities.mcp_server import create_mcp_server
    from agent_bridge.knowledge.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    mcp = create_mcp_server(svc)
    _, structured = asyncio.run(mcp.call_tool("search", {"query": "mysql"}))
    log_id = structured["log_id"]

    listed = client.get("/tool-call-logs", headers={"X-Agent-Bridge-User": "root"})
    detail = client.get(f"/tool-call-logs/{log_id}", headers={"X-Agent-Bridge-User": "root"})

    assert listed.status_code == 200
    assert listed.json()[0]["log_id"] == log_id
    assert detail.status_code == 200
    assert '"query": "mysql"' in detail.json()["request_json"]
    assert detail.json()["response_json"]


def test_tool_call_log_api_filters_by_failure_classification(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.create_tool_call_log(
        log_id="call_failed_upstream",
        actor="root",
        profile_key="safe",
        entrypoint="metamcp_execute",
        source_type=SourceType.mcp_service.value,
        source_key="mysql",
        tool_name="query_sql",
        request={},
        response={},
        status="error",
        failure_stage=FailureStage.upstream_tool.value,
        failure_owner=FailureOwner.upstream_mcp.value,
        error_type="tool_error",
    )
    store.create_tool_call_log(
        log_id="call_failed_policy",
        actor="root",
        profile_key="safe",
        entrypoint="metamcp_execute",
        source_type=SourceType.mcp_service.value,
        source_key="mysql",
        tool_name="query_sql",
        request={},
        response={},
        status="blocked",
        failure_stage=FailureStage.profile_policy.value,
        failure_owner=FailureOwner.policy.value,
        error_type="profile_denied",
    )

    response = client.get(
        "/tool-call-logs",
        params={"failure_owner": FailureOwner.upstream_mcp.value, "failure_stage": FailureStage.upstream_tool.value},
        headers={"X-Agent-Bridge-User": "root"},
    )

    assert response.status_code == 200
    assert [item["log_id"] for item in response.json()] == ["call_failed_upstream"]


def test_tool_call_stats_api_groups_by_dimensions(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.create_tool_call_log(
        log_id="call_stats_api",
        actor="root",
        profile_key="safe",
        entrypoint="metamcp_execute",
        source_type=SourceType.mcp_service.value,
        source_key="mysql",
        tool_name="query_sql",
        request={},
        response={},
        status="success",
        duration_ms=12,
    )

    response = client.get(
        "/tool-call-stats",
        params={"dimensions": "profile_key,source_key,tool_name"},
        headers={"X-Agent-Bridge-User": "root"},
    )

    assert response.status_code == 200
    assert response.json()["dimensions"] == ["profile_key", "source_key", "tool_name"]
    assert response.json()["items"][0]["profile_key"] == "safe"
    assert response.json()["items"][0]["calls"] == 1


def test_capability_catalog_source_and_tool_details(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    client.post(
        "/capabilities/mcp-services",
        json={"service_key": "mysql", "name": "MySQL", "endpoint_url": "https://mysql.test/mcp"},
        headers={"X-Agent-Bridge-User": "root"},
    )
    store = SQLiteStore(wm_paths.db_path)
    store.upsert_mcp_tool(
        service_key="mysql",
        tool_name="query_sql",
        display_name="Query SQL",
        description="Run SQL",
        input_schema={"type": "object", "properties": {"sql": {"type": "string"}}},
        tool_type=ToolType.search.value,
        tags=["sql"],
        examples=[],
    )
    store.create_tool_call_log(
        log_id="call_catalog_detail",
        actor="root",
        profile_key=None,
        entrypoint="metamcp_execute",
        source_type=SourceType.mcp_service.value,
        source_key="mysql",
        tool_name="query_sql",
        request={"sql": "select 1"},
        response={"rows": []},
        status="success",
    )

    source = client.get("/capability-catalog/sources/mcp_service/mysql", headers={"X-Agent-Bridge-User": "root"})
    tool = client.get(
        "/capability-catalog/sources/mcp_service/mysql/tools/query_sql",
        headers={"X-Agent-Bridge-User": "root"},
    )

    assert source.status_code == 200
    assert source.json()["source"]["service_key"] == "mysql"
    assert source.json()["tools"][0]["tool"] == "query_sql"
    assert tool.status_code == 200
    assert tool.json()["tool"]["tool"] == "query_sql"
    assert tool.json()["logs"][0]["log_id"] == "call_catalog_detail"


def test_codegraph_repository_admin_api(tmp_path: Path, wm_paths) -> None:
    repo = _git_repo(tmp_path / "repo")
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)

    created = client.post(
        "/code-repo/repositories",
        json={
            "repo_key": "web-app",
            "name": "Web App",
            "git_url": str(repo),
            "branch": "master",
            "description": "Demo app",
            "tags": ["python"],
        },
        headers={"X-Agent-Bridge-User": "root"},
    )
    listed = client.get("/code-repo/repositories", headers={"X-Agent-Bridge-User": "root"})
    synced = client.post("/code-repo/repositories/web-app/sync", headers={"X-Agent-Bridge-User": "root"})

    assert created.status_code == 200
    assert created.json()["repo_key"] == "web-app"
    assert created.json()["tags"] == ["python"]
    assert listed.status_code == 200
    assert listed.json()[0]["repo_key"] == "web-app"
    assert synced.status_code == 200
    assert synced.json()["status"] == "succeeded"
    assert synced.json()["indexed"] >= 1


def test_codegraph_repository_detail_and_semantic_api(tmp_path: Path, wm_paths) -> None:
    repo = _git_repo(tmp_path / "repo")
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    client.post(
        "/code-repo/repositories",
        json={
            "repo_key": "web-app",
            "name": "Web App",
            "git_url": str(repo),
            "branch": "master",
        },
        headers={"X-Agent-Bridge-User": "root"},
    )
    client.post("/code-repo/repositories/web-app/sync", headers={"X-Agent-Bridge-User": "root"})

    status = client.get("/code-repo/status", headers={"X-Agent-Bridge-User": "root"})
    overview = client.get("/code-repo/repositories/web-app/overview", headers={"X-Agent-Bridge-User": "root"})
    query = client.post(
        "/code-repo/repositories/web-app/query",
        json={"query": "hello", "limit": 5},
        headers={"X-Agent-Bridge-User": "root"},
    )
    callers = client.post(
        "/code-repo/repositories/web-app/callers",
        json={"query": "hello", "limit": 5},
        headers={"X-Agent-Bridge-User": "root"},
    )

    assert status.status_code == 200
    assert "codegraph_installed" in status.json()
    assert overview.status_code == 200
    assert overview.json()["file_count"] >= 1
    assert query.status_code == 200
    assert query.json()["matches"][0]["path"] == "app.py"
    assert callers.status_code == 200
    assert "matches" in callers.json()


def test_understand_summary_returns_empty_payload_when_graph_missing(tmp_path: Path, wm_paths) -> None:
    repo = _git_repo(tmp_path / "repo")
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    client.post(
        "/code-repo/repositories",
        json={
            "repo_key": "web-app",
            "name": "Web App",
            "git_url": str(repo),
            "branch": "master",
        },
        headers={"X-Agent-Bridge-User": "root"},
    )

    response = client.get("/code-repo/repositories/web-app/understand/summary", headers={"X-Agent-Bridge-User": "root"})

    assert response.status_code == 200
    assert response.json() == {
        "project_name": None,
        "description": None,
        "languages": [],
        "frameworks": [],
        "modules": [],
        "key_nodes": [],
        "tours": [],
    }


def test_codegraph_repository_explore_api_uses_stdio_mcp(tmp_path: Path, wm_paths) -> None:
    class FakeMcpClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def call_tool(self, project_dir: Path, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            self.calls.append({"project_dir": project_dir, "tool_name": tool_name, "arguments": arguments})
            return {"is_error": False, "structured": {"answer": "ok"}, "content": []}

    repo = _git_repo(tmp_path / "repo")
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    client.post(
        "/code-repo/repositories",
        json={
            "repo_key": "web-app",
            "name": "Web App",
            "git_url": str(repo),
            "branch": "master",
        },
        headers={"X-Agent-Bridge-User": "root"},
    )
    client.post("/code-repo/repositories/web-app/sync", headers={"X-Agent-Bridge-User": "root"})
    fake_mcp = FakeMcpClient()
    app.state.agent_bridge_service.codegraph.mcp_client = fake_mcp

    response = client.post(
        "/code-repo/repositories/web-app/explore",
        json={"query": "hello"},
        headers={"X-Agent-Bridge-User": "root"},
    )

    assert response.status_code == 200
    assert response.json()["repo"] == "web-app"
    assert response.json()["mcp_result"]["structured"] == {"answer": "ok"}
    assert fake_mcp.calls == [
        {
            "project_dir": wm_paths.repos_dir / "web-app",
            "tool_name": "codegraph_explore",
            "arguments": {"query": "hello", "projectPath": str(wm_paths.repos_dir / "web-app")},
        }
    ]


def test_codegraph_repository_admin_api_requires_admin(tmp_path: Path, wm_paths) -> None:
    repo = _git_repo(tmp_path / "repo")
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)

    response = client.post(
        "/code-repo/repositories",
        json={
            "repo_key": "web-app",
            "name": "Web App",
            "git_url": str(repo),
            "branch": "master",
        },
        headers={"X-Agent-Bridge-User": "alice"},
    )

    assert response.status_code == 403
