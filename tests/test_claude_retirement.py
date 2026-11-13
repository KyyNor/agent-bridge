"""Claude 下线护栏：旧配置可读、历史保留、新执行无回退。"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from agent_bridge.agent_runtime.registry import CodingAgentRegistry, UnknownCodingAgentError
from agent_bridge.agent_runtime.types import CodingAgentCapabilities
from agent_bridge.api.app import create_app
from agent_bridge.cli.app import app as cli_app
from agent_bridge.core.config import AgentBackendConfig, AgentRuntimeConfig, save_agent_runtime_config
from agent_bridge.knowledge_management.code_knowledge.ua_client import UnderstandAnythingClient


@pytest.mark.parametrize("retired_slug", ["claude", "legacy-claude"])
def test_legacy_config_starts_without_fallback_and_keeps_history(wm_paths, retired_slug):
    wm_paths.config_dir.mkdir(parents=True, exist_ok=True)
    wm_paths.server_config_path.write_text(
        f'[agents]\ndefault = "{retired_slug}"\n\n'
        f'[agents.{retired_slug}]\ntype = "claude"\neffort = "max"\n\n'
        '[agents.pi]\ntype = "pi"\n', encoding="utf-8",
    )
    original = wm_paths.server_config_path.read_bytes()
    app = create_app(wm_paths, {"root"})
    service = app.state.agent_bridge_service
    agents = service.agents
    headers = {"X-Agent-Bridge-User": "root"}
    client = TestClient(app)
    payload = client.get("/api/v1/agent-runtime/config", headers=headers).json()
    assert payload["default_backend"] == retired_slug
    assert payload["migration_warnings"]
    assert [item["slug"] for item in payload["available_backends"]] == ["pi"]
    assert [item["slug"] for item in payload["backends"]] == ["pi"]
    catalog = client.get("/api/v1/agent-runtime/backends", headers=headers).json()
    assert catalog["migration_warnings"]
    assert catalog["default_backend"] == retired_slug
    assert "command" not in json.dumps(catalog)

    agents.store.agent_runs.create(
        run_key="historical-claude", agent_name="legacy", backend_key="claude",
        prompt="old", output_schema=None, started_at="2026-01-01T00:00:00Z", actor="root",
        status="completed", ok=True, result="historical result",
    )
    before = agents.store.agent_runs.list()
    for backend in (None, "claude", retired_slug):
        result = asyncio.run(agents.run(prompt="new", actor="root", backend_key=backend))
        assert not result.ok
        assert "已停用" in result.error
        assert not result.run_key
        assert not result.run_dir
    assert agents.store.agent_runs.list() == before
    assert not agents.base_run_dir.exists()
    assert agents.store.agent_runs.get("historical-claude")["result"] == "historical result"
    assert wm_paths.server_config_path.read_bytes() == original
    availability = UnderstandAnythingClient(agent_service=agents).check_availability()
    assert not availability.agent_available
    assert "已停用" in availability.message
    configured = UnderstandAnythingClient(agent_service=agents).check_availability_with_config(
        project_dir=wm_paths.root, ua_git_url="https://example.test/ua.git",
    )
    assert not configured.agent_available

    # 只有管理员明确保存支持的默认后端后，默认执行选择才改变。
    response = client.post("/api/v1/agent-runtime/config", headers=headers, json={
        "default_backend": "pi", "backends": [{"slug": "pi", "type": "pi"}],
        "expected_edit_token": payload["edit_token"],
    })
    assert response.status_code == 200
    assert response.json()["migration_warnings"] == []
    assert agents.coding_agents.get().backend_key == "pi"


@pytest.mark.parametrize("payload", [
    {"default_backend": "claude", "backends": []},
    {"default_backend": "dsh", "backends": [{"slug": "claude", "type": "claude"}]},
    {"default_backend": "dsh", "backends": [{"slug": "legacy", "type": "claude"}]},
])
def test_new_claude_configuration_is_rejected_without_writing(wm_paths, payload):
    client = TestClient(create_app(wm_paths, {"root"}))
    original = wm_paths.server_config_path.read_bytes()
    response = client.post("/api/v1/agent-runtime/config", json=payload, headers={"X-Agent-Bridge-User": "root"})
    assert response.status_code == 400
    assert "已停用" in str(response.json())
    assert wm_paths.server_config_path.read_bytes() == original
    with pytest.raises(ValueError, match="已停用"):
        save_agent_runtime_config(wm_paths, AgentRuntimeConfig(
            default_backend="dsh", backends=(AgentBackendConfig(slug="claude", agent_type="claude"),),
        ))


def test_registry_cannot_register_retired_adapter():
    registry = CodingAgentRegistry()
    with pytest.raises(UnknownCodingAgentError, match="已停用"):
        registry.register(SimpleNamespace(backend_key="claude"))


def test_fresh_default_dsh_reports_missing_runtime_without_fallback(wm_paths):
    from agent_bridge.agent_runtime.service import AgentService
    service = AgentService(paths=wm_paths, store=SimpleNamespace(), admins={"root"}, governance=None)
    assert service.coding_agents.keys() == ["dsh"]
    adapter = service.coding_agents.get()
    # DSH start 必须依赖组级 runtime，不会改用另一个 Agent。
    from agent_bridge.agent_runtime.types import CodingAgentRequest
    request = CodingAgentRequest(prompt="hello", cwd=wm_paths.root, mcp_servers={}, setting_sources=[])
    async def consume():
        async for _ in adapter.start(request).updates():
            pass
    with pytest.raises(RuntimeError, match="DSH.*运行时"):
        asyncio.run(consume())
    assert isinstance(adapter.capabilities, CodingAgentCapabilities)


@pytest.mark.parametrize("command", ["use", "sync"])
def test_retired_profile_installers_do_not_write(monkeypatch, tmp_path, command):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
    result = CliRunner().invoke(cli_app, ["profile", command, "dev", "--scope", "project"])
    assert result.exit_code == 1
    assert "已停用" in result.output
    assert not (tmp_path / ".mcp.json").exists()
    assert not (tmp_path / ".claude").exists()


def test_retired_workflow_stays_parseable_and_preflight_guides_migration(wm_paths):
    from agent_bridge.app.service import AgentBridgeService
    from agent_bridge.automation.workflows.definition import WorkflowGraph
    service = AgentBridgeService.create(wm_paths, {"root"})
    service.store.upsert_project_profile(profile_key="default", name="Default", created_by="root")
    definition = {"nodes": [{
        "id": "legacy", "type": "agent", "name": "旧节点", "position": {"x": 0, "y": 0},
        "config": {"prompt": "old", "backend_key": "claude"},
    }], "edges": []}
    assert WorkflowGraph.model_validate(definition).nodes[0].config.backend_key == "claude"
    result = service.workflows.validator.validate(actor="root", workflow={
        "workflow_key": "legacy", "name": "旧工作流", "description": "", "profile_key": "default",
        "status": "active", "workflow_type": "operation", "definition": definition,
    })
    assert not result.valid
    assert any(issue.field == "config.backend_key" and "已停用" in issue.message for issue in result.errors)


def test_strict_save_rejects_retired_alias_and_blank_default(wm_paths):
    for default, retired in [("legacy", ("legacy",)), ("", ())]:
        with pytest.raises(ValueError):
            save_agent_runtime_config(wm_paths, AgentRuntimeConfig(
                default_backend=default, retired_backends=retired,
            ))
