from __future__ import annotations

import json
import shlex
from pathlib import Path

import httpx
from typer.testing import CliRunner

from agent_bridge.client import AgentBridgeClient
from agent_bridge.cli.app import app


runner = CliRunner()


def _write_legacy_profile(root: Path, profile: str, *, scope: str = "project", user_hook=None):
    """旧接入静态夹具；不依赖已退役的安装命令。"""
    config_path = root / ".mcp.json"
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    config.setdefault("mcpServers", {})["agent-bridge"] = {
        "type": "http", "url": "http://localhost:8765/mcp",
        "headers": {"X-Agent-Bridge-MetaMCP-Profile": profile},
    }
    root.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config), encoding="utf-8")
    settings_path = root / ".claude" / ("settings.local.json" if scope == "project" else "settings.json")
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    hooks = [{"type": "command", "command": f"agent-bridge profile sync {profile} --agent-bridge-hook-id agent-bridge-profile-sync"}]
    if user_hook:
        hooks.append(user_hook)
    settings_path.write_text(json.dumps({"hooks": {"Stop": [{"hooks": hooks}]}}), encoding="utf-8")
    guidance = root / "CLAUDE.md" if scope == "project" else root / ".claude" / "CLAUDE.md"
    existing = guidance.read_text() if guidance.exists() else ""
    guidance.write_text(existing + "<!-- agent-bridge:profile-pointer start -->\nold\n<!-- agent-bridge:profile-pointer end -->\n", encoding="utf-8")


def test_wiki_cli_commands_are_not_registered() -> None:
    result = runner.invoke(app, ["wiki"])
    assert result.exit_code == 2
    assert "No such command" in result.output


def test_client_init_system_posts_admin_init(monkeypatch) -> None:
    captured = {}

    def fake_post(url, headers, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return httpx.Response(200, json=None)

    monkeypatch.setattr("agent_bridge.client.httpx.post", fake_post)
    AgentBridgeClient("http://example.test/", "root").init_system()
    assert captured == {
        "url": "http://example.test/api/v1/admin/init",
        "headers": {"X-Agent-Bridge-User": "root"},
        "timeout": 10.0,
    }


def test_client_from_config_uses_linux_user(monkeypatch) -> None:
    monkeypatch.setattr("agent_bridge.client.getpass.getuser", lambda: "kyynor")

    client = AgentBridgeClient.from_config()

    assert client.linux_user == "kyynor"


def test_client_purge_document_sends_confirmation(monkeypatch) -> None:
    captured = {}

    def fake_post(url, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        return httpx.Response(200, json={"slug": "guide", "status": "purged"})

    monkeypatch.setattr("agent_bridge.client.httpx.post", fake_post)
    result = AgentBridgeClient("http://example.test/", "root").purge_document("guide", confirm=True)
    assert result == {"slug": "guide", "status": "purged"}
    assert captured == {
        "url": "http://example.test/api/v1/docs/guide/purge",
        "json": {"confirm": True},
        "headers": {"X-Agent-Bridge-User": "root"},
        "timeout": 10.0,
    }


def test_server_init_calls_client(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def init_system(self):
            calls.append("init_system")

    monkeypatch.setattr("agent_bridge.cli.app.AgentBridgeClient.from_config", lambda: FakeClient())
    result = runner.invoke(app, ["server", "init"])
    assert result.exit_code == 0
    assert "初始化完成" in result.stdout
    assert calls == ["init_system"]


def test_server_status_command(monkeypatch) -> None:
    monkeypatch.setattr("agent_bridge.cli.app.server_status", lambda: {"running": True, "pid": 123})
    result = runner.invoke(app, ["server", "status"])
    assert result.exit_code == 0
    assert "运行中" in result.stdout
    assert "123" in result.stdout


def test_server_status_accepts_root_option(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    def fake_status(paths):
        captured["root"] = paths.root
        return {"running": True, "pid": 123}

    monkeypatch.setattr("agent_bridge.cli.app.server_status", fake_status)
    result = runner.invoke(app, ["server", "status", "--root", str(tmp_path)])

    assert result.exit_code == 0
    assert captured == {"root": tmp_path}


def test_server_start_reports_errors_cleanly(monkeypatch) -> None:
    def fail_start():
        raise OSError("permission denied")

    monkeypatch.setattr("agent_bridge.cli.app.start_server", fail_start)
    result = runner.invoke(app, ["server", "start"])
    output = f"{result.stdout}{result.stderr}"
    assert result.exit_code == 1
    assert "服务错误" in result.stderr
    assert "permission denied" in result.stderr
    assert "Traceback" not in output


def test_client_search_sends_get(monkeypatch) -> None:
    captured = {}

    def fake_get(url, params, headers, timeout):
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        captured["timeout"] = timeout
        return httpx.Response(200, json={"results": []})

    monkeypatch.setattr("agent_bridge.client.httpx.get", fake_get)
    result = AgentBridgeClient("http://example.test/", "root").search("my-kb", "what?", backend="openai", top_k=3)
    assert result == {"results": []}
    assert captured == {
        "url": "http://example.test/api/v1/search",
        "params": {"kb": "my-kb", "q": "what?", "backend": "openai", "top_k": "3"},
        "headers": {"X-Agent-Bridge-User": "root"},
        "timeout": 30.0,
    }


def test_client_ask_sends_post(monkeypatch) -> None:
    captured = {}

    def fake_post(url, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        return httpx.Response(200, json={"answer": "yes", "session_id": "s1"})

    monkeypatch.setattr("agent_bridge.client.httpx.post", fake_post)
    result = AgentBridgeClient("http://example.test/", "root").ask("my-kb", "is it?", backend="openai", session_id="s1")
    assert result == {"answer": "yes", "session_id": "s1"}
    assert captured == {
        "url": "http://example.test/api/v1/ask",
        "json": {"kb": "my-kb", "question": "is it?", "backend": "openai", "session_id": "s1"},
        "headers": {"X-Agent-Bridge-User": "root"},
        "timeout": 60.0,
    }


def test_profile_create_calls_client(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def upsert_profile(self, profile_key, name, description, status):
            calls.append((profile_key, name, description, status))
            return {"profile_key": profile_key, "name": name}

    monkeypatch.setattr("agent_bridge.cli.app.AgentBridgeClient.from_config", lambda: FakeClient())
    result = runner.invoke(app, ["profile", "create", "safe-readonly", "--name", "安全只读"])

    assert result.exit_code == 0
    assert "safe-readonly" in result.stdout
    assert calls == [("safe-readonly", "安全只读", "", "active")]


def test_profile_rules_calls_client(monkeypatch) -> None:
    captured = {}

    class FakeClient:
        def replace_profile_rules(self, profile_key, rules):
            captured["profile_key"] = profile_key
            captured["rules"] = rules
            return {"profile_key": profile_key, "rules": rules}

    monkeypatch.setattr("agent_bridge.cli.app.AgentBridgeClient.from_config", lambda: FakeClient())
    result = runner.invoke(
        app,
        ["profile", "rules", "safe-readonly", "--allow", "mysql", "--deny", "hive"],
    )

    assert result.exit_code == 0
    assert captured["profile_key"] == "safe-readonly"
    assert captured["rules"] == [
        {"source_type": "mcp_service", "source_key": "mysql", "effect": "allow"},
        {"source_type": "mcp_service", "source_key": "hive", "effect": "deny"},
    ]


def test_profile_unuse_lists_both_scopes_and_preserves_user_config(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    project_settings = tmp_path / ".claude" / "settings.local.json"
    project_settings.parent.mkdir(parents=True)
    user_hook = {"type": "command", "command": "echo project-user-hook"}
    project_settings.write_text(
        json.dumps({"hooks": {"Stop": [{"hooks": [user_hook]}]}}),
        encoding="utf-8",
    )
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"existing": {"command": "node"}}}),
        encoding="utf-8",
    )
    (tmp_path / "CLAUDE.md").write_text("keep project guidance\n", encoding="utf-8")

    _write_legacy_profile(tmp_path, "project-profile", user_hook=user_hook)
    _write_legacy_profile(home, "user-profile", scope="user")

    selected = {}

    class Prompt:
        def ask(self):
            return "project"

    def fake_select(message, choices):
        selected["message"] = message
        selected["choices"] = choices
        return Prompt()

    monkeypatch.setattr("agent_bridge.cli.app._stdin_is_interactive", lambda: True)
    monkeypatch.setattr("questionary.select", fake_select)
    result = runner.invoke(app, ["profile", "unuse"], input="y\n")

    assert result.exit_code == 0
    assert "当前项目" in result.stdout
    assert "用户级" in result.stdout
    assert selected["message"] == "选择要卸载的 Profile"
    assert [choice["value"] for choice in selected["choices"]] == ["project", "user"]

    project_config = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
    assert project_config["mcpServers"] == {"existing": {"command": "node"}}
    project_hooks = json.loads(project_settings.read_text(encoding="utf-8"))
    assert project_hooks["hooks"]["Stop"] == [{"hooks": [user_hook]}]
    project_claude = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert "keep project guidance" in project_claude
    assert "agent-bridge:profile-pointer" not in project_claude

    user_config = json.loads((home / ".mcp.json").read_text(encoding="utf-8"))
    assert user_config["mcpServers"]["agent-bridge"]["headers"]["X-Agent-Bridge-MetaMCP-Profile"] == "user-profile"
    assert (home / ".claude" / "settings.json").exists()
    assert "agent-bridge-profile-sync" in (home / ".claude" / "settings.json").read_text(encoding="utf-8")
    assert "agent-bridge:profile-pointer" in (home / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")


def test_profile_unuse_requires_scope_in_noninteractive_mode(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
    _write_legacy_profile(tmp_path, "safe")
    monkeypatch.setattr("agent_bridge.cli.app._stdin_is_interactive", lambda: False)

    result = runner.invoke(app, ["profile", "unuse"])

    assert result.exit_code == 1
    assert "非交互模式下必须指定 scope" in result.stderr


def test_profile_refresh_command_is_removed() -> None:
    result = runner.invoke(app, ["profile", "refresh", "safe", "--scope", "project"])

    assert result.exit_code == 2
    assert "No such command" in result.output


def test_profile_pins_refresh_calls_client(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def refresh_profile_pin_cache(self, profile_key):
            calls.append(profile_key)
            return {"profile_key": profile_key}

    monkeypatch.setattr("agent_bridge.cli.app.AgentBridgeClient.from_config", lambda: FakeClient())
    result = runner.invoke(app, ["profile", "pins", "refresh", "safe"])

    assert result.exit_code == 0
    assert calls == ["safe"]
    assert "profile: safe 自动 Pin 缓存已清理" in result.stdout


def test_client_render_profile_doc_posts_render_endpoint(monkeypatch) -> None:
    captured = {}

    def fake_post(url, headers, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return httpx.Response(200, json={"markdown": "# Safe\n"})

    monkeypatch.setattr("agent_bridge.client.httpx.post", fake_post)
    result = AgentBridgeClient("http://example.test/", "root").render_profile_doc("safe")

    assert result == {"markdown": "# Safe\n"}
    assert captured == {
        "url": "http://example.test/api/v1/capability-profiles/safe/doc/render",
        "headers": {"X-Agent-Bridge-User": "root"},
        "timeout": 10.0,
    }


def test_client_refresh_profile_doc_context_file_posts_refresh_endpoint(monkeypatch) -> None:
    captured = {}

    def fake_post(url, headers, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return httpx.Response(200, json={"profile_doc_path": "/server/profiles/safe.md"})

    monkeypatch.setattr("agent_bridge.client.httpx.post", fake_post)
    result = AgentBridgeClient("http://example.test/", "root").refresh_profile_doc_context_file("safe")

    assert result == {"profile_doc_path": "/server/profiles/safe.md"}
    assert captured == {
        "url": "http://example.test/api/v1/capability-profiles/safe/doc/context-file",
        "headers": {"X-Agent-Bridge-User": "root"},
        "timeout": 10.0,
    }


def test_client_refresh_profile_pin_cache_posts_refresh_endpoint(monkeypatch) -> None:
    captured = {}

    def fake_post(url, headers, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return httpx.Response(200, json={"profile_key": "safe"})

    monkeypatch.setattr("agent_bridge.client.httpx.post", fake_post)
    result = AgentBridgeClient("http://example.test/", "root").refresh_profile_pin_cache("safe")

    assert result == {"profile_key": "safe"}
    assert captured == {
        "url": "http://example.test/api/v1/capability-profiles/safe/pins/refresh",
        "headers": {"X-Agent-Bridge-User": "root"},
        "timeout": 10.0,
    }
