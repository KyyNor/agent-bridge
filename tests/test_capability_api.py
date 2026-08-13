from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agent_bridge.capability_hub.models import FailureOwner, FailureStage, SourceType, ToolType
from agent_bridge.api.app import create_app
from agent_bridge.knowledge_management.code_knowledge.backend import CliCodeGraphBackend
from agent_bridge.knowledge_management.code_knowledge.client import CodeGraphClient
from agent_bridge.storage.sqlite import SQLiteStore


@pytest.fixture(scope="session", autouse=True)
def _capability_page_source_fixture() -> None:
    """用前端入口源码提供页面测试夹具，不依赖工作区残留的 Vite 构建产物。

    夹具写入的目录本就被 gitignore 排除。这里不在 teardown 删除文件，避免
    pytest-xdist 的不同 worker 相互删除仍在使用的共享夹具。
    """
    package_dir = Path(__file__).parents[1] / "src" / "agent_bridge"
    target = package_dir / "static" / "capabilities" / "index.html"
    source = Path(__file__).parents[1] / "frontend" / "capabilities" / "index.html"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


def _git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "--initial-branch=master"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    (path / "app.py").write_text("def hello():\n    return 'world'\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, capture_output=True)
    return path


def _setup_repo_and_kb(tmp_path: Path, client: TestClient):
    """建一个带 guide.md 的 git 仓库 + KB docs + repo-source r1,返回 repo 路径。"""
    repo = _git_repo(tmp_path / "repo")
    (repo / "guide.md").write_text("# Guide\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add guide"], cwd=repo, check=True, capture_output=True)
    client.post("/api/v1/kbs", json={"slug": "docs", "name": "Docs", "description": ""}, headers={"X-Agent-Bridge-User": "root"})
    client.post(
        "/api/v1/code-repo/repositories",
        json={"repo_key": "r1", "name": "R1", "git_url": str(repo), "branch": "master"},
        headers={"X-Agent-Bridge-User": "root"},
    )
    client.post("/api/v1/code-repo/repositories/r1/sync", headers={"X-Agent-Bridge-User": "root"})
    client.post(
        "/api/v1/kbs/docs/repo-sources",
        json={"repo_key": "r1", "include_suffixes": [".md"]},
        headers={"X-Agent-Bridge-User": "root"},
    )
    return repo


def test_mcp_service_registration_api(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)

    created = client.post(
        "/api/v1/capabilities/mcp-services",
        json={
            "service_key": "mysql",
            "name": "MySQL MCP",
            "endpoint_url": "https://mysql.example.test/mcp",
            "headers": {"Authorization": "Bearer secret"},
                "description": "Database tools",
                "tags": ["database"],
                "visibility": "shared",
        },
        headers={"X-Agent-Bridge-User": "root"},
    )
    listed = client.get("/api/v1/capabilities/mcp-services", headers={"X-Agent-Bridge-User": "alice"})

    assert created.status_code == 200
    assert created.json()["service_key"] == "mysql"
    assert listed.status_code == 200
    assert listed.json()[0]["service_key"] == "mysql"
    assert listed.json()[0]["tags"] == ["database"]
    assert listed.json()[0]["headers"] == {"Authorization": "***"}

    summary = client.get(
        "/api/v1/capabilities/mcp-services?summary=true",
        headers={"X-Agent-Bridge-User": "alice"},
    )
    assert summary.status_code == 200
    assert summary.json()[0]["tool_count"] == 0
    assert "headers" not in summary.json()[0]


def test_top_level_mcp_tool_status_api(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    headers = {"X-Agent-Bridge-User": "root"}

    listed = client.get("/api/v1/capabilities/top-level-mcp-tools", headers=headers)
    assert listed.status_code == 200
    names = {item["name"] for item in listed.json()}
    assert {"memory_search", "artifacts_search"}.issubset(names)
    assert "search" not in names
    assert "execute" not in names

    disabled = client.post(
        "/api/v1/capabilities/top-level-mcp-tools/memory_search/status",
        json={"status": "disabled"},
        headers=headers,
    )
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"

    forbidden = client.post(
        "/api/v1/capabilities/top-level-mcp-tools/memory_search/status",
        json={"status": "disabled"},
        headers={"X-Agent-Bridge-User": "alice"},
    )
    assert forbidden.status_code == 403


def test_mcp_service_registration_requires_admin(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)

    response = client.post(
        "/api/v1/capabilities/mcp-services",
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
        "/api/v1/capabilities/mcp-services",
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
        "/api/v1/capabilities/mcp-services",
        json={
            "service_key": "mysql",
            "name": "MySQL Reporting MCP",
            "endpoint_url": "https://mysql.example.test/mcp",
            "description": "Updated description",
            "tags": ["database", "reporting"],
        },
        headers={"X-Agent-Bridge-User": "root"},
    )
    listed = client.get("/api/v1/capabilities/mcp-services", headers={"X-Agent-Bridge-User": "root"})

    assert updated.status_code == 200
    assert updated.json()["headers"] == {"Authorization": "Bearer secret"}
    assert listed.json()[0]["headers"] == {"Authorization": "***"}
    assert listed.json()[0]["tags"] == ["database", "reporting"]


def test_agent_bridge_spa_serves_html(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)

    response = client.get("/agent-bridge/", headers={"X-Agent-Bridge-User": "root"})

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "智能中枢" in response.text


def test_agent_bridge_history_routes_serve_the_vue_entrypoint(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)

    response = client.get("/agent-bridge/workflow/demo/edit", headers={"X-Agent-Bridge-User": "root"})

    assert response.status_code == 200
    assert "智能中枢" in response.text


def test_agent_bridge_trailing_slash_and_api_boundary(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)

    assert client.get("/agent-bridge", follow_redirects=False).headers["location"].endswith("/agent-bridge/")
    assert client.get("/api/v1/capabilities/mcp-services", headers={"X-Agent-Bridge-User": "root"}).status_code == 200
    assert client.get("/capabilities/mcp-services", headers={"X-Agent-Bridge-User": "root"}).status_code == 404
    assert client.get("/api/v1/not-found", headers={"X-Agent-Bridge-User": "root"}).status_code == 404
    assert client.get("/static/capabilities/not-found.js").status_code == 404


def test_root_redirects_to_management_spa(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/agent-bridge/"


def test_capability_static_assets_are_served(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)

    css = client.get("/static/capabilities/index.html")
    assert css.status_code == 200
    assert b"\xe6\x99\xba\xe8\x83\xbd\xe4\xb8\xad\xe6\x9e\xa2" in css.content


def test_capability_admin_page_is_chinese_control_console(wm_paths) -> None:
    from agent_bridge.web.pages import capability_admin_page

    html = capability_admin_page()
    assert "智能中枢" in html
    assert "AGENT_BRIDGE_DEFAULT_USER" not in html

def test_capability_static_assets_use_chinese_labels(wm_paths) -> None:
    from agent_bridge.web.pages import capability_admin_page

    html = capability_admin_page()
    assert "智能中枢" in html

def test_capability_admin_page_uses_modal_service_form_and_no_refresh_buttons(wm_paths) -> None:
    from agent_bridge.web.pages import capability_admin_page

    html = capability_admin_page()
    assert 'id="app"' in html

def test_capability_admin_page_has_phase2_views_and_modals(wm_paths) -> None:
    from agent_bridge.web.pages import capability_admin_page

    html = capability_admin_page()
    assert 'id="app"' in html

def test_capability_static_assets_support_phase2_interactions(wm_paths) -> None:
    from agent_bridge.web.pages import capability_admin_page

    html = capability_admin_page()
    assert "script" in html

def test_capability_static_assets_support_query_route_state(wm_paths) -> None:
    from agent_bridge.web.pages import capability_admin_page

    html = capability_admin_page()
    assert "html" in html

def test_capability_admin_page_has_profile_dialog_and_tool_filters(wm_paths) -> None:
    from agent_bridge.web.pages import capability_admin_page

    html = capability_admin_page()
    assert "智能中枢" in html

def test_capability_static_assets_render_filterable_tool_table_and_two_column_modal(wm_paths) -> None:
    from agent_bridge.web.pages import capability_admin_page

    html = capability_admin_page()
    assert 'id="app"' in html

def test_mcp_service_status_and_tools_api(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    client.post(
        "/api/v1/capabilities/mcp-services",
        json={
            "service_key": "mysql",
            "name": "MySQL MCP",
            "endpoint_url": "https://mysql.example.test/mcp",
        },
        headers={"X-Agent-Bridge-User": "root"},
    )

    disabled = client.post(
        "/api/v1/capabilities/mcp-services/mysql/status",
        json={"status": "disabled"},
        headers={"X-Agent-Bridge-User": "root"},
    )
    tools = client.get("/api/v1/capabilities/mcp-services/mysql/tools", headers={"X-Agent-Bridge-User": "root"})

    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    assert tools.status_code == 400
    assert tools.json()["detail"] == "MCP service is not enabled"


def test_mcp_tool_type_api_requires_admin_and_updates_tool(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    client.post(
        "/api/v1/capabilities/mcp-services",
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
        "/api/v1/capabilities/mcp-services/mysql/tools/query_sql/type",
        json={"tool_type": "search"},
        headers={"X-Agent-Bridge-User": "alice"},
    )
    invalid = client.put(
        "/api/v1/capabilities/mcp-services/mysql/tools/query_sql/type",
        json={"tool_type": "other"},
        headers={"X-Agent-Bridge-User": "root"},
    )
    updated = client.put(
        "/api/v1/capabilities/mcp-services/mysql/tools/query_sql/type",
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
        "/api/v1/capabilities/mcp-services",
        json={"service_key": "mysql", "name": "MySQL", "endpoint_url": "https://mysql.test/mcp"},
        headers={"X-Agent-Bridge-User": "root"},
    )
    client.post(
        "/api/v1/capabilities/mcp-services",
        json={"service_key": "hive", "name": "Hive", "endpoint_url": "https://hive.test/mcp"},
        headers={"X-Agent-Bridge-User": "root"},
    )

    created = client.post(
        "/api/v1/capability-profiles",
        json={"profile_key": "safe-readonly", "name": "安全只读", "description": "", "status": "active"},
        headers={"X-Agent-Bridge-User": "root"},
    )
    rules = client.put(
        "/api/v1/capability-profiles/safe-readonly/rules",
        json={"rules": [{"source_type": "mcp_service", "source_key": "hive", "effect": "deny"}]},
        headers={"X-Agent-Bridge-User": "root"},
    )
    listed = client.get("/api/v1/capability-profiles", headers={"X-Agent-Bridge-User": "root"})
    detail = client.get("/api/v1/capability-profiles/safe-readonly", headers={"X-Agent-Bridge-User": "root"})
    catalog = client.get(
        "/api/v1/capability-catalog",
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
        "/api/v1/capability-profiles",
        json={"profile_key": "safe-readonly", "name": "安全只读", "description": "", "status": "active"},
        headers={"X-Agent-Bridge-User": "root"},
    )
    app.state.agent_bridge_service.store.create_kb(
        "frontend-docs",
        "Frontend Docs",
        "",
        "root",
        owner_group_key=app.state.agent_bridge_service.access.maintenance_group_key,
        visibility="group",
    )

    saved = client.put(
        "/api/v1/capability-profiles/safe-readonly/resources",
        json={"resources": [{"resource_type": "wiki_kb", "resource_key": "frontend-docs"}]},
        headers={"X-Agent-Bridge-User": "root"},
    )
    detail = client.get("/api/v1/capability-profiles/safe-readonly", headers={"X-Agent-Bridge-User": "root"})

    assert saved.status_code == 200
    assert saved.json()["resource_rules"][0]["resource_key"] == "frontend-docs"
    assert detail.json()["resource_rules"][0]["resource_type"] == "wiki_kb"


def test_profile_pin_api_round_trip(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.upsert_project_profile(
        profile_key="safe",
        name="Safe",
        description="",
        status="active",
        created_by="root",
    )
    client.post(
        "/api/v1/capabilities/mcp-services",
        json={"service_key": "mysql", "name": "MySQL", "endpoint_url": "https://mysql.test/mcp"},
        headers={"X-Agent-Bridge-User": "root"},
    )
    client.post(
        "/api/v1/capabilities/mcp-services/mysql/status",
        json={"status": "enabled"},
        headers={"X-Agent-Bridge-User": "root"},
    )
    store.upsert_mcp_tool(
        service_key="mysql",
        tool_name="query_users",
        display_name="Query Users",
        description="",
        input_schema={},
        tool_type="search",
        tags=[],
        examples=[],
    )
    store.replace_profile_source_rules(
        "safe",
        [{"source_type": SourceType.mcp_service.value, "source_key": "mysql", "effect": "allow"}],
    )

    saved = client.put(
        "/api/v1/capability-profiles/safe/pins",
        json={"pins": [{"service_key": "mysql", "tool_type": "search"}]},
        headers={"X-Agent-Bridge-User": "root"},
    )
    settings = client.put(
        "/api/v1/capability-profiles/safe/pins/settings",
        json={"mode": "count", "count": 2},
        headers={"X-Agent-Bridge-User": "root"},
    )
    refreshed = client.post(
        "/api/v1/capability-profiles/safe/pins/refresh",
        headers={"X-Agent-Bridge-User": "root"},
    )
    fetched = client.get("/api/v1/capability-profiles/safe/pins", headers={"X-Agent-Bridge-User": "root"})

    assert saved.status_code == 200
    assert [(group["service_key"], group["tool_type"], group["source"]) for group in saved.json()["groups"]] == [
        ("mysql", "search", "manual")
    ]
    assert [tool["generated_tool_name"] for tool in saved.json()["tools"]] == ["pin_mysql_query_users"]
    assert settings.status_code == 200
    assert settings.json()["settings"]["mode"] == "count"
    assert refreshed.status_code == 200
    assert refreshed.json()["profile_key"] == "safe"
    assert fetched.status_code == 200
    assert [(group["service_key"], group["tool_type"], group["source"]) for group in fetched.json()["groups"]] == [
        ("mysql", "search", "manual")
    ]
    assert [tool["generated_tool_name"] for tool in fetched.json()["tools"]] == ["pin_mysql_query_users"]


def test_profile_doc_api_render_and_notes(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.upsert_project_profile(profile_key="safe", name="Safe", description="", status="active", created_by="root")

    notes = client.put(
        "/api/v1/capability-profiles/safe/doc/manual-notes",
        json={"manual_notes": "Manual policy"},
        headers={"X-Agent-Bridge-User": "root"},
    )
    assert notes.status_code == 200
    assert "Manual policy" in notes.json()["markdown"]

    rendered = client.post(
        "/api/v1/capability-profiles/safe/doc/render",
        headers={"X-Agent-Bridge-User": "root"},
    )
    assert rendered.status_code == 200
    assert "# Agent Bridge Profile：Safe" in rendered.json()["markdown"]
    assert "Manual policy" in rendered.json()["markdown"]
    assert rendered.json()["profile_doc_path"] == str(wm_paths.profiles_dir / "safe.md")


def test_builtin_wiki_kbs_api_returns_status_summary(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    client.post(
        "/api/v1/kbs",
        json={"slug": "frontend-docs", "name": "Frontend Docs", "description": ""},
        headers={"X-Agent-Bridge-User": "root"},
    )

    response = client.get("/api/v1/builtin/wiki/kbs", headers={"X-Agent-Bridge-User": "root"})

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
    client.post("/api/v1/admin/init", headers={"X-Agent-Bridge-User": "root"})

    created = client.post(
        "/api/v1/kbs",
        json={"slug": "frontend-docs", "name": "Frontend Docs", "description": ""},
        headers={"X-Agent-Bridge-User": "root"},
    )
    uploaded = client.post(
        "/api/v1/docs",
        data={"kb": "frontend-docs", "later": "true"},
        files={"file": ("Guide.md", source.read_bytes(), "text/markdown")},
        headers={"X-Agent-Bridge-User": "root"},
    )
    blocked_upload = client.post(
        "/api/v1/docs",
        data={"kb": "frontend-docs", "later": "true"},
        files={"file": ("Guide.md", source.read_bytes(), "text/markdown")},
        headers={"X-Agent-Bridge-User": "alice"},
    )
    docs = client.get("/api/v1/docs", params={"kb": "frontend-docs"}, headers={"X-Agent-Bridge-User": "root"})
    detail = client.get("/api/v1/docs/guide", headers={"X-Agent-Bridge-User": "root"})
    status = client.get("/api/v1/status", headers={"X-Agent-Bridge-User": "root"})
    summary = client.get("/api/v1/builtin/wiki/kbs", headers={"X-Agent-Bridge-User": "root"})

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

    response = client.get("/api/v1/backends", headers={"X-Agent-Bridge-User": "root"})

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

    response = client.get("/api/v1/backends", headers={"X-Agent-Bridge-User": "alice"})

    assert response.status_code == 403


def test_backend_create_and_update_forward_edit_token(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    headers = {"X-Agent-Bridge-User": "root"}

    created = client.post(
        "/api/v1/backends",
        json={"slug": "mock-backend", "backend_type": "mock", "expected_edit_token": None},
        headers=headers,
    )
    assert created.status_code == 200

    updated = client.put(
        "/api/v1/backends/mock-backend",
        json={"timeout": 90, "expected_edit_token": created.json()["edit_token"]},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["timeout"] == 90


def test_frontend_stats_view_uses_calls_field_from_backend() -> None:
    source = Path("frontend/capabilities/src/views/monitoring/StatsView.vue").read_text(encoding="utf-8")

    assert "callCount" in source
    assert "s.count" not in source


def test_frontend_knowledge_navigation_groups_document_code_and_config() -> None:
    source = Path("frontend/capabilities/src/App.vue").read_text(encoding="utf-8")
    router = Path("frontend/capabilities/src/router/index.ts").read_text(encoding="utf-8")

    assert "label: '资源管理'" not in source
    assert "key: 'knowledge', label: '文档知识'" in source
    assert "key: 'code-repos', label: '代码知识'" in source
    assert "key: 'system-config', label: '系统管理'" in source
    assert "KnowledgeProcessingConfigView" in router
    assert "CodeRepoView" in router
    assert "path: '/system-config'" in router
    assert "path: '/code-repos/:routeKey" in router
    assert "BuiltinsView" not in router
    assert "path: '/builtins'" not in router
    assert source.count("label: '知识管理'") == 1
    assert source.index("label: '调用观测'") < source.index("label: '系统管理'")


def test_frontend_knowledge_copy_uses_document_and_code_knowledge_names() -> None:
    knowledge = Path("frontend/capabilities/src/views/knowledge/KnowledgeView.vue").read_text(encoding="utf-8")
    profiles = Path("frontend/capabilities/src/views/capabilities/ProfileDetailView.vue").read_text(encoding="utf-8")

    assert "创建文档知识" in knowledge
    assert "暂无文档知识，点击「创建文档知识」开始" in knowledge
    assert "允许访问的文档知识" in profiles
    assert "请先在文档知识中添加" in profiles
    assert "请先在代码知识中添加" in profiles


def test_frontend_codegraph_detail_uses_single_query_panel() -> None:
    source = Path("frontend/capabilities/src/views/knowledge/CodeRepoDetailView.vue").read_text(encoding="utf-8")

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
    assert "log_retention_days" in source
    assert "运行日志保留" in source
    assert "mcp_timeout_seconds" in source
    assert "MCP 超时" in source
    assert "doc_sync_cron" in source
    assert "workflow_start_time" in source
    assert "workflow_stop_time" in source
    assert "知识同步" in source
    assert "工作流调度" in source
    assert "最近进度" in source
    assert "当前执行" in source
    assert "grid-cols-[12rem_minmax(0,10rem)_1fr]" in source


@pytest.mark.codegraph_cli
def test_kb_repo_source_api_saves_config_and_syncs_filtered_files(wm_paths, tmp_path) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    repo = _git_repo(tmp_path / "repo")
    (repo / "guide.md").write_text("# Guide\n", encoding="utf-8")
    (repo / "notes.txt").write_text("notes\n", encoding="utf-8")
    (repo / "skip.py").write_text("print('skip')\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "docs"], cwd=repo, check=True, capture_output=True)
    client.post(
        "/api/v1/kbs",
        json={"slug": "docs", "name": "Docs", "description": ""},
        headers={"X-Agent-Bridge-User": "root"},
    )
    client.post(
        "/api/v1/code-repo/repositories",
        json={
            "repo_key": "docs-repo",
            "name": "Docs Repo",
            "git_url": str(repo),
            "branch": "master",
        },
        headers={"X-Agent-Bridge-User": "root"},
    )
    client.post("/api/v1/code-repo/repositories/docs-repo/sync", headers={"X-Agent-Bridge-User": "root"})

    saved = client.post(
        "/api/v1/kbs/docs/repo-sources",
        json={"repo_key": "docs-repo", "include_suffixes": [".md", ".txt"]},
        headers={"X-Agent-Bridge-User": "root"},
    )
    listed = client.get("/api/v1/kbs/docs/repo-sources", headers={"X-Agent-Bridge-User": "root"})
    synced = client.post(
        "/api/v1/kbs/docs/repo-sources/docs-repo/sync",
        headers={"X-Agent-Bridge-User": "root"},
    )
    docs = client.get("/api/v1/docs?kb=docs", headers={"X-Agent-Bridge-User": "root"})

    assert saved.status_code == 200, saved.text
    assert saved.json()["include_suffixes"] == [".md", ".txt"]
    assert listed.status_code == 200
    assert listed.json()[0]["repo_key"] == "docs-repo"
    assert synced.status_code == 200, synced.text
    assert synced.json()["added"] == 2
    assert synced.json()["removed"] == 0
    assert {item["title"] for item in docs.json()} == {"guide", "notes"}


def test_frontend_knowledge_view_exposes_git_repo_source_controls() -> None:
    source = Path("frontend/capabilities/src/views/knowledge/KnowledgeView.vue").read_text(encoding="utf-8")
    # Git 数据源控件在 9e1aea7 拆分到 KnowledgeRepoSourcesPanel 子组件，视图通过
    # 该面板加载和维护仓库同步状态，suffix 过滤与删除按钮的实现落在子组件文件里。
    panel = Path("frontend/capabilities/src/components/knowledge/KnowledgeRepoSourcesPanel.vue").read_text(encoding="utf-8")
    client = Path("frontend/capabilities/src/api/client.ts").read_text(encoding="utf-8")

    assert "Git 数据源" in source
    assert "include_suffixes" in panel
    assert "deleteRepoSource" in panel
    assert "listKbRepoSources" in client
    assert "syncKbRepoSource" in client
    assert "deleteKbRepoSource" in client


def test_tool_call_log_api_returns_full_payload(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    from agent_bridge.capability_hub.gateway.metamcp import create_mcp_server
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    mcp = create_mcp_server(svc)
    _, structured = asyncio.run(mcp.call_tool("search", {"query": "mysql"}))
    log_id = structured["log_id"]

    listed = client.get("/api/v1/tool-call-logs", headers={"X-Agent-Bridge-User": "root"})
    page = client.get(
        "/api/v1/tool-call-logs?paginated=true&limit=10",
        headers={"X-Agent-Bridge-User": "root"},
    )
    detail = client.get(f"/api/v1/tool-call-logs/{log_id}", headers={"X-Agent-Bridge-User": "root"})

    assert listed.status_code == 200
    assert listed.json()[0]["log_id"] == log_id
    assert page.status_code == 200
    assert page.json()["items"][0]["log_id"] == log_id
    assert "request_json" not in page.json()["items"][0]
    assert "response_json" not in page.json()["items"][0]
    assert detail.status_code == 200
    assert '"query": "mysql"' in detail.json()["request_json"]
    assert detail.json()["response_json"]


def test_tool_call_log_api_filters_by_failure_classification(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    store = SQLiteStore(wm_paths.db_path, wm_paths.log_db_path)
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
        "/api/v1/tool-call-logs",
        params={"failure_owner": FailureOwner.upstream_mcp.value, "failure_stage": FailureStage.upstream_tool.value},
        headers={"X-Agent-Bridge-User": "root"},
    )

    assert response.status_code == 200
    assert [item["log_id"] for item in response.json()] == ["call_failed_upstream"]


def test_tool_call_log_api_paginates_search_and_status_counts(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    store = SQLiteStore(wm_paths.db_path, wm_paths.log_db_path)
    store.init_schema()
    for index, status in enumerate(("success", "error", "blocked")):
        store.create_tool_call_log(
            log_id=f"shared_api_call_{index}",
            actor="shared-api-actor",
            profile_key="safe",
            entrypoint="metamcp_execute",
            source_type=SourceType.mcp_service.value,
            source_key="shared-api-source",
            tool_name="query_sql",
            request={},
            response={},
            status=status,
        )

    response = client.get(
        "/api/v1/tool-call-logs",
        params={"paginated": "true", "search": "shared-api", "status": "error", "limit": 1, "offset": -3},
        headers={"X-Agent-Bridge-User": "root"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["log_id"] for item in body["items"]] == ["shared_api_call_1"]
    assert body["total"] == 1
    assert body["limit"] == 1
    assert body["offset"] == 0
    assert body["counts"] == {
        "all": 3,
        "success": 1,
        "failed": 1,
        "running": 0,
        "error": 1,
        "blocked": 1,
    }


def test_tool_call_log_api_paginates_beyond_two_hundred_rows(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    store = SQLiteStore(wm_paths.db_path, wm_paths.log_db_path)
    store.init_schema()
    for index in range(205):
        store.create_tool_call_log(
            log_id=f"bulk_api_call_{index:03d}",
            actor="bulk-actor",
            profile_key="safe",
            entrypoint="metamcp_execute",
            source_type=SourceType.mcp_service.value,
            source_key="bulk-source",
            tool_name="query_sql",
            request={},
            response={},
            status="success",
        )

    headers = {"X-Agent-Bridge-User": "root"}
    first = client.get(
        "/api/v1/tool-call-logs",
        params={"paginated": "true", "limit": 10, "offset": 0},
        headers=headers,
    ).json()
    last = client.get(
        "/api/v1/tool-call-logs",
        params={"paginated": "true", "limit": 10, "offset": 200},
        headers=headers,
    ).json()

    assert first["total"] == 205
    assert len(first["items"]) == 10
    assert len(last["items"]) == 5
    assert {item["log_id"] for item in first["items"]}.isdisjoint(
        item["log_id"] for item in last["items"]
    )


def test_tool_call_stats_api_groups_by_dimensions(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    store = SQLiteStore(wm_paths.db_path, wm_paths.log_db_path)
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
        "/api/v1/tool-call-stats",
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
        "/api/v1/capabilities/mcp-services",
        json={"service_key": "mysql", "name": "MySQL", "endpoint_url": "https://mysql.test/mcp"},
        headers={"X-Agent-Bridge-User": "root"},
    )
    store = SQLiteStore(wm_paths.db_path, wm_paths.log_db_path)
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

    source = client.get("/api/v1/capability-catalog/sources/mcp_service/mysql", headers={"X-Agent-Bridge-User": "root"})
    tool = client.get(
        "/api/v1/capability-catalog/sources/mcp_service/mysql/tools/query_sql",
        headers={"X-Agent-Bridge-User": "root"},
    )

    assert source.status_code == 200
    assert source.json()["source"]["service_key"] == "mysql"
    assert source.json()["tools"][0]["tool"] == "query_sql"
    assert tool.status_code == 200
    assert tool.json()["tool"]["tool"] == "query_sql"
    assert tool.json()["logs"][0]["log_id"] == "call_catalog_detail"


@pytest.mark.codegraph_cli
def test_codegraph_repository_admin_api(tmp_path: Path, wm_paths) -> None:
    repo = _git_repo(tmp_path / "repo")
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)

    created = client.post(
        "/api/v1/code-repo/repositories",
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
    listed = client.get("/api/v1/code-repo/repositories", headers={"X-Agent-Bridge-User": "root"})
    synced = client.post("/api/v1/code-repo/repositories/web-app/sync", headers={"X-Agent-Bridge-User": "root"})

    assert created.status_code == 200
    assert created.json()["repo_key"] == "web-app"
    assert created.json()["tags"] == ["python"]
    assert listed.status_code == 200
    assert listed.json()[0]["repo_key"] == "web-app"
    assert synced.status_code == 200
    assert synced.json()["status"] == "succeeded"
    assert synced.json()["indexed"] >= 1


@pytest.mark.codegraph_cli
def test_codegraph_repository_detail_and_semantic_api(tmp_path: Path, wm_paths) -> None:
    repo = _git_repo(tmp_path / "repo")
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    client.post(
        "/api/v1/code-repo/repositories",
        json={
            "repo_key": "web-app",
            "name": "Web App",
            "git_url": str(repo),
            "branch": "master",
        },
        headers={"X-Agent-Bridge-User": "root"},
    )
    client.post("/api/v1/code-repo/repositories/web-app/sync", headers={"X-Agent-Bridge-User": "root"})

    status = client.get("/api/v1/code-repo/status", headers={"X-Agent-Bridge-User": "root"})
    overview = client.get("/api/v1/code-repo/repositories/web-app/overview", headers={"X-Agent-Bridge-User": "root"})
    query = client.post(
        "/api/v1/code-repo/repositories/web-app/query",
        json={"query": "hello", "limit": 5},
        headers={"X-Agent-Bridge-User": "root"},
    )
    callers = client.post(
        "/api/v1/code-repo/repositories/web-app/callers",
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


def test_codegraph_query_api_returns_503_when_backend_is_unavailable(tmp_path: Path, wm_paths) -> None:
    repo = _git_repo(tmp_path / "repo")
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    created = client.post(
        "/api/v1/code-repo/repositories",
        json={
            "repo_key": "web-app",
            "name": "Web App",
            "git_url": str(repo),
            "branch": "master",
        },
        headers={"X-Agent-Bridge-User": "root"},
    )
    assert created.status_code == 200
    local_repo = wm_paths.repos_dir / "web-app"
    local_repo.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", str(repo), str(local_repo)], check=True, capture_output=True)
    backend_client = CodeGraphClient()
    backend_client._available = False
    app.state.agent_bridge_service.codegraph.backend = CliCodeGraphBackend(client=backend_client)

    response = client.post(
        "/api/v1/code-repo/repositories/web-app/query",
        json={"query": "hello", "limit": 5},
        headers={"X-Agent-Bridge-User": "root"},
    )

    assert response.status_code == 503
    assert "CodeGraph CLI 不可用" in response.json()["detail"]


def test_understand_summary_returns_empty_payload_when_graph_missing(tmp_path: Path, wm_paths) -> None:
    repo = _git_repo(tmp_path / "repo")
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    client.post(
        "/api/v1/code-repo/repositories",
        json={
            "repo_key": "web-app",
            "name": "Web App",
            "git_url": str(repo),
            "branch": "master",
        },
        headers={"X-Agent-Bridge-User": "root"},
    )

    response = client.get("/api/v1/code-repo/repositories/web-app/understand/summary", headers={"X-Agent-Bridge-User": "root"})

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


@pytest.mark.codegraph_cli
def test_codegraph_repository_explore_api_uses_stdio_mcp(tmp_path: Path, wm_paths) -> None:
    class FakeMcpClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def call_tool(
            self,
            project_dir: Path,
            tool_name: str,
            arguments: dict[str, Any],
            timeout: float = 60.0,
        ) -> dict[str, Any]:
            self.calls.append(
                {"project_dir": project_dir, "tool_name": tool_name, "arguments": arguments, "timeout": timeout}
            )
            return {"is_error": False, "structured": {"answer": "ok"}, "content": []}

    repo = _git_repo(tmp_path / "repo")
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    client.post(
        "/api/v1/code-repo/repositories",
        json={
            "repo_key": "web-app",
            "name": "Web App",
            "git_url": str(repo),
            "branch": "master",
        },
        headers={"X-Agent-Bridge-User": "root"},
    )
    client.post("/api/v1/sync-config", json={"code_sync_cron": "0 * * * *", "mcp_timeout_seconds": 150}, headers={"X-Agent-Bridge-User": "root"})
    client.post("/api/v1/code-repo/repositories/web-app/sync", headers={"X-Agent-Bridge-User": "root"})
    fake_mcp = FakeMcpClient()
    app.state.agent_bridge_service.codegraph.backend.mcp_client = fake_mcp

    response = client.post(
        "/api/v1/code-repo/repositories/web-app/explore",
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
            "timeout": 150.0,
        }
    ]


def test_sync_config_api_round_trips_log_retention_days(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)

    saved = client.post(
        "/api/v1/sync-config",
        json={"code_sync_cron": "0 * * * *", "log_retention_days": 90},
        headers={"X-Agent-Bridge-User": "root"},
    )
    loaded = client.get("/api/v1/sync-config", headers={"X-Agent-Bridge-User": "root"})

    assert saved.status_code == 200
    assert saved.json()["log_retention_days"] == 90
    assert loaded.status_code == 200
    assert loaded.json()["log_retention_days"] == 90


def test_sync_config_api_round_trips_artifact_search_cache_ttl(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)

    saved = client.post(
        "/api/v1/sync-config",
        json={"code_sync_cron": "0 * * * *", "artifact_search_cache_ttl_hours": 12},
        headers={"X-Agent-Bridge-User": "root"},
    )
    loaded = client.get("/api/v1/sync-config", headers={"X-Agent-Bridge-User": "root"})

    assert saved.status_code == 200
    assert saved.json()["artifact_search_cache_ttl_hours"] == 12
    assert loaded.status_code == 200
    assert loaded.json()["artifact_search_cache_ttl_hours"] == 12


def test_codegraph_repository_admin_api_requires_admin(tmp_path: Path, wm_paths) -> None:
    repo = _git_repo(tmp_path / "repo")
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)

    response = client.post(
        "/api/v1/code-repo/repositories",
        json={
            "repo_key": "web-app",
            "name": "Web App",
            "git_url": str(repo),
            "branch": "master",
        },
        headers={"X-Agent-Bridge-User": "alice"},
    )

    assert response.status_code == 403


def test_execute_capability_api_uses_service_tool_name_params(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)

    response = client.post(
        "/api/v1/capabilities/execute",
        json={
            "service": "built-in",
            "tool_name": "load_skill",
            "params": {"skill_name": "design_workflow"},
        },
        headers={"X-Agent-Bridge-User": "root"},
    )

    assert response.status_code == 200
    assert "service" not in response.json()
    assert "tool" not in response.json()
    assert "tool_name" not in response.json()
    assert response.json()["result"]["skill_name"] == "design_workflow"


def test_frontend_workflow_view_exposes_workflow_management() -> None:
    source = Path("frontend/capabilities/src/views/workflow/WorkflowView.vue").read_text(encoding="utf-8")
    canvas = Path("frontend/capabilities/src/views/workflow/WorkflowEditorCanvas.vue").read_text(encoding="utf-8")
    run_graph = Path("frontend/capabilities/src/views/workflow/WorkflowRunGraph.vue").read_text(encoding="utf-8")

    assert "workflow_key" in source
    assert "profile_key" in source
    assert "workflow_type" in source
    assert "操作" in source
    assert "总结" in source
    assert "WorkflowEditorCanvas" in source
    assert "WorkflowNodeConfigPanel" in source
    assert "workflow_js" not in source
    assert "parseWorkflowDag" not in source
    assert "WorkflowDagGraph" not in source
    assert "@vue-flow/core" in canvas
    assert "agent_run_key" in run_graph
    assert "script_run_id" in run_graph
    assert "warning" in run_graph
    assert "skipped" in run_graph
    assert "cancelled" in run_graph
    assert "routeMode === 'tasks'" in source
    assert "searchArtifacts" in source
    assert "openArtifactHistory" in source
    assert "requestClearWorkflow" in source
    assert "RunEventTimeline" in source
    assert "启用" in source
    assert "停用" in source


def test_frontend_scripts_view_exposes_runtime_guide_and_test_headers() -> None:
    source = Path("frontend/capabilities/src/views/system/ScriptsView.vue").read_text(encoding="utf-8")
    client_source = Path("frontend/capabilities/src/api/client.ts").read_text(encoding="utf-8")

    assert "使用指引" in source
    assert "showGuide" in source
    assert "<Dialog" in source
    assert "main(envelope)" in source
    assert "params (JSON 对象)" in source
    assert "让智能体协助编写" in source
    assert 'skill_name":"design_script"' in source
    assert "execute service='built-in'" in source
    assert "workflow_get_task" in source
    assert "workflow_run_log" in source
    assert "Workflow Headers" in source
    assert "workflow_key" in source
    assert "run_id" in source
    assert "X-Agent-Bridge-MetaMCP-Profile" in client_source
    assert "X-Agent-Bridge-Workflow-Run-Id" in client_source


def test_frontend_tool_debug_view_exposes_profile_scoped_execute_debugging() -> None:
    app_source = Path("frontend/capabilities/src/App.vue").read_text(encoding="utf-8")
    router_source = Path("frontend/capabilities/src/router/index.ts").read_text(encoding="utf-8")
    view_source = Path("frontend/capabilities/src/views/capabilities/ToolDebugView.vue").read_text(encoding="utf-8")
    client_source = Path("frontend/capabilities/src/api/client.ts").read_text(encoding="utf-8")

    assert "tool-debug" in router_source
    assert "工具调试" in router_source
    assert "ToolDebugView" in router_source
    assert "按能力平面选择并手动调试对外提供的工具" not in app_source
    assert "能力平面" in view_source
    assert "执行工具" in view_source
    assert "调试结果" in view_source
    assert "OpenAPI" in view_source
    assert "MCP" in view_source
    assert "params 必须是 JSON 对象" in view_source
    assert "api.executeCapability" in view_source
    assert "X-Agent-Bridge-MetaMCP-Profile" in client_source
    assert "executeCapability" in client_source


@pytest.mark.codegraph_cli
def test_sync_changes_imports_new_files(wm_paths, tmp_path: Path) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    repo = _setup_repo_and_kb(tmp_path, client)
    # 首次同步:guide.md 是新文件(app.py 不在 include_suffixes,被跳过)
    r = client.post("/api/v1/kbs/docs/repo-sources/r1/sync", headers={"X-Agent-Bridge-User": "root"})
    assert r.status_code == 200, r.text
    assert r.json()["added"] == 1
    assert r.json()["removed"] == 0
    assert r.json()["updated"] == 0
    docs = client.get("/api/v1/docs?kb=docs", headers={"X-Agent-Bridge-User": "root"}).json()
    assert {d["title"] for d in docs} == {"guide"}


@pytest.mark.codegraph_cli
def test_sync_changes_modifies_changed_file_as_delete_then_add(wm_paths, tmp_path: Path) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    repo = _setup_repo_and_kb(tmp_path, client)
    # 首次导入 guide.md
    client.post("/api/v1/kbs/docs/repo-sources/r1/sync", headers={"X-Agent-Bridge-User": "root"})
    docs_before = client.get("/api/v1/docs?kb=docs", headers={"X-Agent-Bridge-User": "root"}).json()
    doc_id_before = docs_before[0]["id"]
    # 修改文件内容
    (repo / "guide.md").write_text("# Guide v2\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "v2"], cwd=repo, check=True, capture_output=True)
    client.post("/api/v1/code-repo/repositories/r1/sync", headers={"X-Agent-Bridge-User": "root"})
    # 再次同步:应为 updated=1
    r = client.post("/api/v1/kbs/docs/repo-sources/r1/sync", headers={"X-Agent-Bridge-User": "root"})
    assert r.json()["updated"] == 1
    assert r.json()["added"] == 0
    docs_after = client.get("/api/v1/docs?kb=docs", headers={"X-Agent-Bridge-User": "root"}).json()
    # doc_id 变化(先删后加)
    assert docs_after[0]["id"] != doc_id_before
    unchanged = client.post("/api/v1/kbs/docs/repo-sources/r1/sync", headers={"X-Agent-Bridge-User": "root"})
    assert unchanged.json()["unchanged"] == 1
    assert unchanged.json()["added"] == 0
    assert unchanged.json()["removed"] == 0
    assert unchanged.json()["updated"] == 0


@pytest.mark.codegraph_cli
def test_sync_changes_refreshes_repo_before_diff(wm_paths, tmp_path: Path) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    repo = _setup_repo_and_kb(tmp_path, client)
    client.post("/api/v1/kbs/docs/repo-sources/r1/sync", headers={"X-Agent-Bridge-User": "root"})

    (repo / "guide.md").write_text("# Guide from upstream\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "upstream update"], cwd=repo, check=True, capture_output=True)

    r = client.post("/api/v1/kbs/docs/repo-sources/r1/sync", headers={"X-Agent-Bridge-User": "root"})

    assert r.status_code == 200, r.text
    assert r.json()["updated"] == 1
    assert r.json()["unchanged"] == 0


@pytest.mark.codegraph_cli
def test_sync_changes_handles_duplicate_slugs_stably(wm_paths, tmp_path: Path) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    repo = _git_repo(tmp_path / "repo")
    (repo / "a").mkdir()
    (repo / "b").mkdir()
    (repo / "a" / "guide.md").write_text("# First\n", encoding="utf-8")
    (repo / "b" / "guide.md").write_text("# Second\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "duplicate guides"], cwd=repo, check=True, capture_output=True)
    client.post("/api/v1/kbs", json={"slug": "docs", "name": "Docs", "description": ""}, headers={"X-Agent-Bridge-User": "root"})
    client.post(
        "/api/v1/code-repo/repositories",
        json={"repo_key": "r1", "name": "R1", "git_url": str(repo), "branch": "master"},
        headers={"X-Agent-Bridge-User": "root"},
    )
    client.post(
        "/api/v1/kbs/docs/repo-sources",
        json={"repo_key": "r1", "include_suffixes": [".md"]},
        headers={"X-Agent-Bridge-User": "root"},
    )

    first = client.post("/api/v1/kbs/docs/repo-sources/r1/sync", headers={"X-Agent-Bridge-User": "root"})
    second = client.post("/api/v1/kbs/docs/repo-sources/r1/sync", headers={"X-Agent-Bridge-User": "root"})

    assert first.status_code == 200, first.text
    assert first.json()["added"] == 2
    assert second.status_code == 200, second.text
    assert second.json()["updated"] == 0
    assert second.json()["unchanged"] == 2


@pytest.mark.codegraph_cli
def test_sync_changes_removes_deleted_file(wm_paths, tmp_path: Path) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    repo = _setup_repo_and_kb(tmp_path, client)
    # 建 mock backend target 以便删除时生成 sync job
    store = SQLiteStore(wm_paths.db_path)
    kb = store.get_kb_by_slug("docs")
    store.ensure_backend_target(kb["id"], "mock", "mock")
    client.post("/api/v1/kbs/docs/repo-sources/r1/sync", headers={"X-Agent-Bridge-User": "root"})
    client.post("/api/v1/sync", json={"all_users": False}, headers={"X-Agent-Bridge-User": "root"})
    # 删除文件
    (repo / "guide.md").unlink()
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "del"], cwd=repo, check=True, capture_output=True)
    client.post("/api/v1/code-repo/repositories/r1/sync", headers={"X-Agent-Bridge-User": "root"})
    r = client.post("/api/v1/kbs/docs/repo-sources/r1/sync", headers={"X-Agent-Bridge-User": "root"})
    assert r.json()["removed"] == 1
    # active 文档应已清空(guide 被软删)
    docs = client.get("/api/v1/docs?kb=docs", headers={"X-Agent-Bridge-User": "root"}).json()
    assert docs == []
    # 应生成 delete 同步任务
    jobs = client.get("/api/v1/status", headers={"X-Agent-Bridge-User": "root"}).json()["jobs"]
    assert any(j["operation"] == "delete" for j in jobs)


@pytest.mark.codegraph_cli
def test_delete_kb_repo_source_cancels_pending_create_without_delete_job(wm_paths, tmp_path: Path) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    _setup_repo_and_kb(tmp_path, client)
    # 建 mock backend target 以便删除时生成 sync job
    store = SQLiteStore(wm_paths.db_path)
    kb = store.get_kb_by_slug("docs")
    store.ensure_backend_target(kb["id"], "mock", "mock")
    # 导入 git 文档
    client.post("/api/v1/kbs/docs/repo-sources/r1/sync", headers={"X-Agent-Bridge-User": "root"})
    # 删除数据源
    r = client.post("/api/v1/kbs/docs/repo-sources/r1/delete", headers={"X-Agent-Bridge-User": "root"})
    assert r.status_code == 200, r.text
    assert r.json()["deleted_docs"] == 1
    # active 文档清空
    docs = client.get("/api/v1/docs?kb=docs", headers={"X-Agent-Bridge-User": "root"}).json()
    assert docs == []
    # 还没上传过后端:取消 create,不生成 delete
    jobs = client.get("/api/v1/status", headers={"X-Agent-Bridge-User": "root"}).json()["jobs"]
    assert [(j["operation"], j["status"]) for j in jobs] == [("create", "cancelled")]
    assert not any(j["operation"] == "delete" for j in jobs)
    # 数据源已解绑(list 为空)
    sources = client.get("/api/v1/kbs/docs/repo-sources", headers={"X-Agent-Bridge-User": "root"}).json()
    assert sources == []


@pytest.mark.codegraph_cli
def test_delete_kb_repo_source_removes_synced_docs_and_generates_delete_jobs(wm_paths, tmp_path: Path) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    _setup_repo_and_kb(tmp_path, client)
    store = SQLiteStore(wm_paths.db_path)
    kb = store.get_kb_by_slug("docs")
    store.ensure_backend_target(kb["id"], "mock", "mock")
    client.post("/api/v1/kbs/docs/repo-sources/r1/sync", headers={"X-Agent-Bridge-User": "root"})
    client.post("/api/v1/sync", json={"all_users": False}, headers={"X-Agent-Bridge-User": "root"})

    r = client.post("/api/v1/kbs/docs/repo-sources/r1/delete", headers={"X-Agent-Bridge-User": "root"})

    assert r.status_code == 200, r.text
    assert r.json()["deleted_docs"] == 1
    docs = client.get("/api/v1/docs?kb=docs", headers={"X-Agent-Bridge-User": "root"}).json()
    assert docs == []
    jobs = client.get("/api/v1/status", headers={"X-Agent-Bridge-User": "root"}).json()["jobs"]
    assert any(j["operation"] == "delete" for j in jobs)
    # 数据源已解绑(list 为空)
    sources = client.get("/api/v1/kbs/docs/repo-sources", headers={"X-Agent-Bridge-User": "root"}).json()
    assert sources == []
