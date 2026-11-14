"""Hindsight 配置、原生注入和 HTTP 权限边界回归。"""
from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from agent_bridge.api.app import create_app
from agent_bridge.agent_workspace.capability import WorkspaceSelection
from agent_bridge.agent_workspace.launcher import LinuxIdentity
from agent_bridge.core.domain import AccessDenied, ConflictError, ValidationError
from agent_bridge.knowledge_management.memory.native import (
    CONFIG_NAME, PACKAGE_NAME, PACKAGE_SPEC, PACKAGE_VERSION, allowed_gateway_path,
)


@pytest.fixture
def setup(wm_paths, tmp_path):
    app = create_app(paths=wm_paths, admins={"root"})
    service = app.state.agent_bridge_service
    service.access.upsert_group(actor="root", group_key="team", name="小组")
    service.access.create_user(actor="root", user_id="alice")
    service.access.set_user_group(actor="root", user_id="alice", group_key="team")
    service.governance.upsert_profile("alice", "work", "工作", "", "active")
    service.memory.create_block("alice", "memory", "团队记忆", "")
    service.memory.set_profile_binding("alice", "work", "memory", enabled=True)
    service.memory.backend_config.save(actor="root", backend="hindsight", url="https://memory.example", api_token="upstream-secret")
    capability = service.pi.capabilities.issue(user_id="alice", profile_key="work", owner_group_key="team")
    identity = LinuxIdentity("linux-user", os.getuid(), os.getgid(), tmp_path)
    return app, service, WorkspaceSelection("work", capability), identity


def install_pi_fixture(directory):
    package = directory / "node_modules" / PACKAGE_NAME
    (package / "dist").mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({"version": PACKAGE_VERSION}))
    (package / "dist" / "pi.js").touch()


def prepare(service, selection, identity, directory, harness="pi"):
    directory.mkdir(exist_ok=True)
    if harness == "pi":
        install_pi_fixture(directory)
    else:
        (directory / "agent-bridge-plugins.txt").write_text(PACKAGE_SPEC + "\n")
    return service.memory.native.prepare(config_dir=directory, identity=identity, workspace=selection,
                                         env={"HINDSIGHT_API_TOKEN": "global-secret", "HINDSIGHT_BANK_ID": "global-bank"},
                                         mcp_url="http://127.0.0.1:8321/mcp", harness=harness)


@respx.mock
def test_config_is_private_masked_and_optimistically_locked(setup):
    app, service, _, _ = setup
    manager = service.memory.backend_config
    config = manager.public()
    assert config["api_token_set"] and "upstream-secret" not in json.dumps(config)
    assert manager.path.stat().st_mode & 0o777 == 0o600
    manager.save(actor="root", backend="hindsight", url="https://new.example", expected_edit_token=config["edit_token"])
    assert manager.read()["api_token"] == "upstream-secret"
    with pytest.raises(ConflictError):
        manager.save(actor="root", backend="claude-mem", url="", expected_edit_token=config["edit_token"])
    with pytest.raises(ValidationError):
        manager.save(actor="root", backend="invalid", url="")
    client = TestClient(app)
    assert client.get("/api/v1/memory/backend-config", headers={"X-Agent-Bridge-User": "alice"}).status_code == 403
    respx.get("https://new.example/v1/version").respond(200, json={"api_version": "0.4.15"})
    assert client.post("/api/v1/memory/backend-config/test", headers={"X-Agent-Bridge-User": "root"}).status_code == 200


def test_invalid_config_never_falls_back_to_claude_mem(setup):
    _, service, _, _ = setup
    service.memory.backend_config.path.write_text("invalid")
    with pytest.raises(ValidationError, match="不会自动"):
        _ = service.memory.backend


@pytest.mark.parametrize("harness", ["pi", "dsh"])
@respx.mock
def test_native_startup_freezes_bank_and_upstream_without_persisting_secrets(setup, tmp_path, harness):
    _, service, selection, identity = setup
    create = respx.put(url__regex=r"https://memory.example/v1/default/banks/agent-bridge-.*").respond(200, json={})
    directory = tmp_path / harness
    env, patch = prepare(service, selection, identity, directory, harness)
    config = json.loads((directory / CONFIG_NAME).read_text())
    assert create.call_count == 1
    assert not config["dynamicBankId"] and not config["autoUpdate"] and not config["autoSeed"]
    assert not config["codebaseSurvey"] and config["gitIngest"] == "none"
    assert config["autoInject"] == "recall"
    assert env["HINDSIGHT_API_TOKEN"] == selection.capability.token
    assert "HINDSIGHT_BANK_ID" not in env
    assert "upstream-secret" not in json.dumps(env)
    assert selection.capability.token not in (directory / CONFIG_NAME).read_text()
    assert "global-secret" not in (directory / CONFIG_NAME).read_text()
    if harness == "pi":
        assert len(json.loads((directory / "settings.json").read_text())["extensions"]) == 1
    else:
        assert json.loads(patch.read_text()) == [{"id": "hindsight", "disabled": False}]
    service.memory.backend_config.save(actor="root", backend="claude-mem", url="https://new.example", api_token="new-secret")
    grant = service.memory.native.require_grant(selection.capability)
    assert grant.settings["url"] == "https://memory.example"
    assert grant.bank_id == config["bankId"]


@pytest.mark.parametrize("harness", ["pi", "dsh"])
def test_unbound_memory_disables_collection_and_native_tools(setup, tmp_path, harness):
    _, service, selection, identity = setup
    service.memory.set_profile_binding("alice", "work", "memory", enabled=False)
    directory = tmp_path / harness
    env, patch = prepare(service, selection, identity, directory, harness)
    config = json.loads((directory / CONFIG_NAME).read_text())
    assert config["disabled"] and not config["retainSessions"]
    assert "HINDSIGHT_API_TOKEN" not in env
    if harness == "pi":
        assert not json.loads((directory / "settings.json").read_text())["extensions"]
    else:
        assert json.loads(patch.read_text())[0]["disabled"]
    with pytest.raises(AccessDenied):
        service.memory.native.require_grant(selection.capability)


@respx.mock
def test_restart_without_memory_clears_old_grant(setup, tmp_path):
    _, service, selection, identity = setup
    respx.put(url__regex=r"https://memory.example/v1/default/banks/agent-bridge-.*").respond(200, json={})
    directory = tmp_path / "pi"
    prepare(service, selection, identity, directory)
    service.memory.backend_config.save(actor="root", backend="claude-mem", url="https://memory.example")
    # 同一 capability 在实例重启后不得残留原生工具抑制或旧网关授权。
    service.memory.native.prepare(config_dir=directory, identity=identity, workspace=selection, env={},
                                  mcp_url="http://127.0.0.1:8321/mcp", harness="pi")
    with pytest.raises(AccessDenied):
        service.memory.native.require_grant(selection.capability)
    from agent_bridge.knowledge_management.memory.native import NATIVE_MEMORY_ACTIVE
    with service.memory.native.mcp_scope(selection.capability):
        assert not NATIVE_MEMORY_ACTIVE.get()


@respx.mock
def test_hindsight_read_adapter_normalizes_existing_memory_views(setup):
    _, service, _, _ = setup
    bank = service.store.memory.ensure_hindsight_bank_id("memory")
    upstream = "https://memory.example/v1/default/banks/" + bank
    respx.post(upstream + "/memories/recall").respond(200, json={"results": [{"id": "fact", "text": "团队规则", "score": 1}]})
    result = service.memory.search(actor="alice", profile_key="work", query="规则")
    assert result["items"][0]["summary"] == "团队规则"
    assert result["items"][0]["content_preview"] == "团队规则"
    respx.get(upstream + "/documents").respond(200, json={"items": [{"id": "chat:session", "created_at": "2026-11-14T00:00:00Z"}], "total": 2})
    result = service.memory.timeline(actor="alice", profile_key="work", limit=1)
    assert result["items"][0]["event_type"] == "document" and result["next_cursor"] == "1"
    respx.get(upstream + "/memories/fact").respond(200, json={"id": "fact", "text": "规则详情"})
    result = service.memory.get_observation(actor="alice", profile_key="work", observation_id="fact")
    assert result["item"]["content"] == "规则详情"


@respx.mock
def test_gateway_checks_bank_capability_binding_ownership_and_sanitizes_upstream(setup, tmp_path):
    app, service, selection, identity = setup
    respx.put(url__regex=r"https://memory.example/v1/default/banks/agent-bridge-.*").respond(200, json={})
    prepare(service, selection, identity, tmp_path / "pi")
    grant = service.memory.native.require_grant(selection.capability)
    root = "/api/v1/memory/hindsight/v1/default/banks/" + grant.bank_id
    upstream = "https://memory.example/v1/default/banks/" + grant.bank_id
    headers = {"Authorization": "Bearer " + selection.capability.token}
    client = TestClient(app)
    route = respx.post(upstream + "/memories").respond(200, json={"operation_id": "operation"})
    assert client.post(root + "/memories", headers=headers, json={"items": [{"content": "记住配置"}]}).status_code == 200
    assert route.calls[0].request.headers["authorization"] == "Bearer upstream-secret"
    assert selection.capability.token not in str(route.calls[0].request.headers)
    respx.get(upstream + "/operations/operation").respond(200, json={"status": "completed"})
    assert client.get(root + "/operations/operation", headers=headers).status_code == 200
    assert client.get(root + "/operations/operation").status_code == 403
    assert client.get(root.replace(grant.bank_id, "other") + "/documents", headers=headers).status_code == 403
    assert client.delete(root, headers=headers).status_code in {403, 404}
    assert client.post(root + "/import", headers=headers, json={}).status_code == 403
    respx.get(upstream + "/config").respond(200, json={"config": {"llm_api_key": "hidden", "store_document_text": True}})
    assert client.get(root + "/config", headers=headers).json() == {"config": {"store_document_text": True}}
    respx.post(upstream + "/reflect").respond(401, text="private-upstream-body")
    failed = client.post(root + "/reflect", headers=headers, json={"query": "查配置"})
    assert failed.status_code == 502 and "private-upstream-body" not in failed.text
    service.memory.set_profile_binding("alice", "work", "memory", enabled=False)
    assert client.post(root + "/memories", headers=headers, json={}).status_code == 403
    service.memory.set_profile_binding("alice", "work", "memory", enabled=True)
    service.access.remove_user_group(actor="root", user_id="alice")
    assert client.get(root + "/documents", headers=headers).status_code == 403
    service.pi.capabilities.revoke(selection.capability.token)
    assert client.get(root + "/documents", headers=headers).status_code == 403


def test_bank_is_stable_and_not_reused_on_block_recreation(setup):
    _, service, _, _ = setup
    repository = service.store.memory
    first = repository.ensure_hindsight_bank_id("memory")
    assert repository.ensure_hindsight_bank_id("memory") == first
    repository.delete_memory_block("memory")
    service.memory.create_block("alice", "memory", "同名新记忆", "")
    assert repository.ensure_hindsight_bank_id("memory") != first


@pytest.mark.parametrize("method,path,allowed", [
    ("POST", "/memories", True), ("POST", "/reflect", True), ("POST", "/memories/recall", True),
    ("GET", "/operations/op", True), ("GET", "/documents/chat:session", True),
    ("PATCH", "/pages/nodes/kp-1", True), ("DELETE", "/memories", False),
    ("POST", "/import", False), ("PATCH", "/config", False), ("GET", "/../other/documents", False),
    ("GET", "/%2fother/documents", False),
])
def test_gateway_path_allowlist(method, path, allowed):
    assert allowed_gateway_path(method, "/v1/default/banks/bank" + path, "bank") is allowed


@respx.mock
def test_symlink_config_rejected_before_plugin_launch(setup, tmp_path):
    _, service, selection, identity = setup
    respx.put(url__regex=r"https://memory.example/v1/default/banks/agent-bridge-.*").respond(200, json={})
    directory = tmp_path / "symlink"
    directory.mkdir()
    victim = tmp_path / "victim"
    victim.write_text("protected")
    (directory / CONFIG_NAME).symlink_to(victim)
    with pytest.raises(ValidationError, match="软链"):
        prepare(service, selection, identity, directory)
    assert victim.read_text() == "protected"


@respx.mock
def test_capability_expiry_and_shared_identity_boundaries(setup, tmp_path, monkeypatch):
    app, service, selection, identity = setup
    respx.put(url__regex=r"https://memory.example/v1/default/banks/agent-bridge-.*").respond(200, json={})
    prepare(service, selection, identity, tmp_path / "personal")
    now = selection.capability.expires_at_monotonic + 1
    monkeypatch.setattr("agent_bridge.agent_workspace.capability.time.monotonic", lambda: now)
    root = "/api/v1/memory/hindsight/v1/version"
    assert TestClient(app).get(root, headers={"Authorization": "Bearer " + selection.capability.token}).status_code == 403
    service.store.dsh_config.save_group_config(group_key="team", linux_user="linux-user", default_model="", api_key=None, clear_api_key=False, updated_by="root")
    shared = service.pi.capabilities.issue(user_id="linux-user", profile_key="work", owner_group_key="team",
                                           ttl_seconds=None, key="shared-runtime:linux-user")
    prepare(service, WorkspaceSelection("work", shared), identity, tmp_path / "shared")
    assert service.memory.native.require_grant(shared).block_key == "memory"
    service.store.dsh_config.save_group_config(group_key="team", linux_user="new-linux-user", default_model="", api_key=None, clear_api_key=False, updated_by="root")
    with pytest.raises(AccessDenied, match="映射"):
        service.memory.native.require_grant(shared)


@respx.mock
def test_native_memory_hides_and_rejects_duplicate_mcp_tools(setup, tmp_path):
    import asyncio

    _, service, selection, identity = setup
    respx.put(url__regex=r"https://memory.example/v1/default/banks/agent-bridge-.*").respond(200, json={})
    prepare(service, selection, identity, tmp_path / "pi")
    provider = service.capabilities.builtin_providers["memory"]
    with service.memory.native.mcp_scope(selection.capability):
        assert provider.list_tools("alice", "work") == []
        assert provider.list_resources("alice", "work") == []
        with pytest.raises(AccessDenied, match="原生"):
            asyncio.run(provider.execute("alice", "search", {"query": "secret", "block": "other"}, "work"))
    assert provider.list_tools("alice", "work")


@pytest.mark.parametrize("memory_enabled", [True, False])
@pytest.mark.parametrize("entry", ["pi-web", "pi-tui", "dsh-web", "dsh-tui"])
@respx.mock
def test_four_launch_plans_load_official_plugin_before_start(setup, tmp_path, entry, memory_enabled, monkeypatch):
    from agent_bridge.agent_workspace.adapters.pi import PiWorkspaceAdapter
    from agent_bridge.agent_workspace.adapters.dsh import DshWorkspaceAdapter
    from agent_bridge.agent_workspace.terminal_adapter import PiTerminalAdapter, DshTerminalAdapter

    _, service, selection, identity = setup
    service.memory.set_profile_binding("alice", "work", "memory", enabled=memory_enabled)
    if memory_enabled:
        respx.put(url__regex=r"https://memory.example/v1/default/banks/agent-bridge-.*").respond(200, json={})

    class Installer:
        def run_once(self, *, command, env, cwd, identity, timeout_seconds):
            assert "upstream-secret" not in str(env)
            assert selection.capability.token not in str(env)
            if "npm" in Path(command[0]).name:
                install_pi_fixture(Path(command[command.index("--prefix") + 1]))
            return 0, ""

    import shutil
    real_which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/pi" if name == "pi" else real_which(name))
    adapters = {"pi-web": PiWorkspaceAdapter, "pi-tui": PiTerminalAdapter,
                "dsh-web": DshWorkspaceAdapter, "dsh-tui": DshTerminalAdapter}
    adapter = adapters[entry](configs=service.dsh_configs, launcher=Installer(),
                              mcp_url="http://127.0.0.1:8321/mcp", memory=service.memory.native)
    plan = adapter.build_launch_plan(identity=identity, runtime_owner="alice", scope="personal", runtime_key="alice",
                                     port=49123, workspace=selection, model_binding={}, log_path=tmp_path / "run.log")
    config = json.loads(Path(plan.env["HINDSIGHT_CONFIG"]).read_text())
    assert config["disabled"] is not memory_enabled
    assert config["retainSessions"] is memory_enabled
    if memory_enabled:
        assert plan.env["HINDSIGHT_API_TOKEN"] == selection.capability.token
    else:
        assert "HINDSIGHT_API_TOKEN" not in plan.env
    if entry.startswith("dsh"):
        assert "agent-bridge-hindsight.patch.json" in str(plan.command)
        assert PACKAGE_SPEC in (Path(plan.env["DSH_HOME"]) / "agent-bridge-plugins.txt").read_text()
        patch = Path(plan.env["DSH_HOME"]) / "agent-bridge-hindsight.patch.json"
        assert json.loads(patch.read_text())[0]["disabled"] is not memory_enabled
    else:
        extensions = json.loads((Path(plan.env["PI_CODING_AGENT_DIR"]) / "settings.json").read_text())["extensions"]
        if memory_enabled:
            assert extensions[0].endswith("/dist/pi.js")
        else:
            assert extensions == []
