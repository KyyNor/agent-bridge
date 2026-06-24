from __future__ import annotations

import json
import shlex
from pathlib import Path

import httpx
from typer.testing import CliRunner

from agent_bridge.client import AgentBridgeClient
from agent_bridge.cli.app import app


runner = CliRunner()


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
        "url": "http://example.test/admin/init",
        "headers": {"X-Agent-Bridge-User": "root"},
        "timeout": 10.0,
    }


def test_client_from_config_uses_environment_user(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_BRIDGE_USER", "kyynor")

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
        "url": "http://example.test/docs/guide/purge",
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
        "url": "http://example.test/search",
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
        "url": "http://example.test/ask",
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


def test_profile_use_writes_project_config(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    class FakeClient:
        def render_profile_doc(self, profile_key):
            raise AssertionError("profile use should not render or write local profile docs")

    monkeypatch.setattr("agent_bridge.cli.app.AgentBridgeClient.from_config", lambda: FakeClient())
    result = runner.invoke(
        app,
        [
            "profile",
            "use",
            "safe-readonly",
            "--scope",
            "project",
            "--url",
            "http://127.0.0.1:8765/mcp",
        ],
    )

    config = tmp_path / ".mcp.json"
    assert result.exit_code == 0
    assert config.exists()
    assert "safe-readonly" in config.read_text(encoding="utf-8")


def test_profile_use_preserves_existing_servers(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    config = tmp_path / ".mcp.json"
    config.write_text(
        json.dumps({"mcpServers": {"existing": {"command": "node"}}}),
        encoding="utf-8",
    )

    class FakeClient:
        def render_profile_doc(self, profile_key):
            raise AssertionError("profile use should not render or write local profile docs")

    monkeypatch.setattr("agent_bridge.cli.app.AgentBridgeClient.from_config", lambda: FakeClient())
    result = runner.invoke(
        app,
        [
            "profile",
            "use",
            "safe-readonly",
            "--scope",
            "project",
            "--url",
            "http://127.0.0.1:8765/mcp",
        ],
    )

    data = json.loads(config.read_text(encoding="utf-8"))
    assert result.exit_code == 0
    assert "existing" in data["mcpServers"]
    assert "agent-bridge" in data["mcpServers"]
    assert "agent-capability-hub" not in data["mcpServers"]


def test_profile_use_writes_stable_channel_without_profile_files(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    class FakeClient:
        def render_profile_doc(self, profile_key):
            raise AssertionError("profile use should not render or write local profile docs")

    monkeypatch.setattr("agent_bridge.cli.app.AgentBridgeClient.from_config", lambda: FakeClient())
    result = runner.invoke(
        app,
        ["profile", "use", "safe", "--scope", "project", "--url", "http://127.0.0.1:8765/mcp"],
    )

    assert result.exit_code == 0
    data = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
    assert "agent-bridge" in data["mcpServers"]
    assert "agent-capability-hub" not in data["mcpServers"]
    assert not (tmp_path / ".agent-bridge" / "profiles" / "safe.md").exists()
    assert not (tmp_path / "CLAUDE.md").exists()
    assert not (tmp_path / "AGENTS.md").exists()


def test_profile_use_installs_claude_mem_compatible_hooks(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    class FakeClient:
        def render_profile_doc(self, profile_key):
            raise AssertionError("profile use should not render or write local profile docs")

        def get_profile_memory(self, profile_key):
            return {"profile_key": profile_key, "block_key": "dev-memory", "enabled": 1}

    monkeypatch.setattr("agent_bridge.cli.app.AgentBridgeClient.from_config", lambda: FakeClient())
    result = runner.invoke(
        app,
        ["profile", "use", "safe-readonly", "--scope", "project", "--url", "http://127.0.0.1:8765/mcp", "--yes"],
    )

    assert result.exit_code == 0
    settings = json.loads((tmp_path / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    hooks = settings["hooks"]
    assert hooks["Setup"][0]["matcher"] == "*"
    assert hooks["Setup"][0]["hooks"][0]["timeout"] == 300
    assert hooks["Setup"][0]["hooks"][0]["command"].startswith("agent-bridge memory hook claude-code version-check")
    setup_argv = shlex.split(hooks["Setup"][0]["hooks"][0]["command"])
    assert setup_argv[setup_argv.index("--scope") + 1] == "project"
    assert hooks["SessionStart"][0]["matcher"] == "startup|resume|clear|compact"
    session_start_argv = [shlex.split(hook["command"]) for hook in hooks["SessionStart"][0]["hooks"]]
    assert [argv[4] for argv in session_start_argv] == ["session-start"]
    assert all(argv[argv.index("--scope") + 1] == "project" for argv in session_start_argv)
    assert all(argv[argv.index("--matcher") + 1] == "startup|resume|clear|compact" for argv in session_start_argv)
    assert all("|" not in argv for argv in session_start_argv)
    assert "'startup|resume|clear|compact'" in hooks["SessionStart"][0]["hooks"][0]["command"]
    assert hooks["PostToolUse"][0]["matcher"] == "*"
    assert hooks["PreToolUse"][0]["matcher"] == "Read"
    assert hooks["Stop"][0]["hooks"][0]["timeout"] == 120


def test_profile_use_preserves_user_hooks(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    settings_path = tmp_path / ".claude" / "settings.local.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo user"}]}]}}),
        encoding="utf-8",
    )

    class FakeClient:
        def render_profile_doc(self, profile_key):
            raise AssertionError("profile use should not render or write local profile docs")

        def get_profile_memory(self, profile_key):
            return {"profile_key": profile_key, "block_key": None, "enabled": 1}

    monkeypatch.setattr("agent_bridge.cli.app.AgentBridgeClient.from_config", lambda: FakeClient())
    result = runner.invoke(
        app,
        ["profile", "use", "safe-readonly", "--scope", "project", "--url", "http://127.0.0.1:8765/mcp", "--yes"],
    )

    assert result.exit_code == 0
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert settings["hooks"]["Stop"][0]["hooks"] == [{"type": "command", "command": "echo user"}]
    assert "agent-bridge memory hook claude-code summarize" in settings["hooks"]["Stop"][1]["hooks"][0]["command"]


def test_profile_use_does_not_call_render_profile_doc(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    class FakeClient:
        def render_profile_doc(self, profile_key):
            raise AssertionError("profile use should not render or write local profile docs")

    monkeypatch.setattr("agent_bridge.cli.app.AgentBridgeClient.from_config", lambda: FakeClient())
    result = runner.invoke(app, ["profile", "use", "safe", "--scope", "project"])

    assert result.exit_code == 0
    assert (tmp_path / ".mcp.json").exists()


def test_profile_use_does_not_need_render_profile_doc_when_preserving_config(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    config = tmp_path / ".mcp.json"
    original = {"mcpServers": {"existing": {"command": "node"}}}
    config.write_text(json.dumps(original), encoding="utf-8")

    class FakeClient:
        def render_profile_doc(self, profile_key):
            raise AssertionError("profile use should not render or write local profile docs")

    monkeypatch.setattr("agent_bridge.cli.app.AgentBridgeClient.from_config", lambda: FakeClient())
    result = runner.invoke(app, ["profile", "use", "safe", "--scope", "project", "--yes"])

    assert result.exit_code == 0
    data = json.loads(config.read_text(encoding="utf-8"))
    assert "existing" in data["mcpServers"]
    assert "agent-bridge" in data["mcpServers"]


def test_profile_use_writes_user_scope_channel_without_profile_files(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr("pathlib.Path.home", lambda: home)

    class FakeClient:
        def render_profile_doc(self, profile_key):
            raise AssertionError("profile use should not render or write local profile docs")

    monkeypatch.setattr("agent_bridge.cli.app.AgentBridgeClient.from_config", lambda: FakeClient())
    result = runner.invoke(app, ["profile", "use", "safe", "--scope", "user"])

    assert result.exit_code == 0
    data = json.loads((home / ".mcp.json").read_text(encoding="utf-8"))
    assert "agent-bridge" in data["mcpServers"]
    assert not (home / ".agent-bridge" / "profiles" / "safe.md").exists()
    assert not (home / ".claude" / "CLAUDE.md").exists()
    assert not (home / ".codex" / "AGENTS.md").exists()


def test_profile_use_migrates_legacy_server_and_preserves_existing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    config = tmp_path / ".mcp.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "existing": {"command": "node"},
                    "agent-capability-hub": {"url": "old"},
                }
            }
        ),
        encoding="utf-8",
    )

    class FakeClient:
        def render_profile_doc(self, profile_key):
            raise AssertionError("profile use should not render or write local profile docs")

    monkeypatch.setattr("agent_bridge.cli.app.AgentBridgeClient.from_config", lambda: FakeClient())
    result = runner.invoke(app, ["profile", "use", "safe", "--scope", "project", "--yes"])

    data = json.loads(config.read_text(encoding="utf-8"))
    assert result.exit_code == 0
    assert "existing" in data["mcpServers"]
    assert "agent-bridge" in data["mcpServers"]
    assert "agent-capability-hub" not in data["mcpServers"]


def test_profile_use_does_not_modify_existing_claude_or_agents_files(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "CLAUDE.md").write_text(
        "keep me\n<!-- agent-bridge:profile-pointer start -->\n@/old/profile.md\n"
        "<!-- agent-bridge:profile-pointer end -->\n",
        encoding="utf-8",
    )
    (tmp_path / "AGENTS.md").write_text(
        "keep agents\n<!-- agent-bridge:profile-pointer start -->\nold pointer\n"
        "<!-- agent-bridge:profile-pointer end -->\n",
        encoding="utf-8",
    )

    class FakeClient:
        def render_profile_doc(self, profile_key):
            raise AssertionError("profile use should not render or write local profile docs")

    monkeypatch.setattr("agent_bridge.cli.app.AgentBridgeClient.from_config", lambda: FakeClient())
    result = runner.invoke(app, ["profile", "use", "new-profile", "--scope", "project", "--yes"])

    assert result.exit_code == 0
    claude = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "keep me" in claude
    assert "@/old/profile.md" in claude
    assert "keep agents" in agents
    assert "old pointer" in agents
    assert not (tmp_path / ".agent-bridge" / "profiles" / "new-profile.md").exists()


def test_profile_use_prompts_for_scope_when_missing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
    monkeypatch.setattr("agent_bridge.cli.app._stdin_is_interactive", lambda: True)
    monkeypatch.setattr("questionary.select", lambda *args, **kwargs: type("Prompt", (), {"ask": lambda self: "project"})())

    class FakeClient:
        def render_profile_doc(self, profile_key):
            raise AssertionError("profile use should not render or write local profile docs")

    monkeypatch.setattr("agent_bridge.cli.app.AgentBridgeClient.from_config", lambda: FakeClient())

    result = runner.invoke(
        app,
        [
            "profile",
            "use",
            "safe-readonly",
            "--url",
            "http://127.0.0.1:8765/mcp",
        ],
    )

    assert result.exit_code == 0
    assert (tmp_path / ".mcp.json").exists()
    assert "已写入:" in result.output


def test_profile_use_requires_scope_in_non_interactive_mode(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("agent_bridge.cli.app._stdin_is_interactive", lambda: False)

    result = runner.invoke(
        app,
        ["profile", "use", "safe-readonly", "--url", "http://127.0.0.1:8765/mcp"],
    )

    assert result.exit_code == 1
    assert "非交互模式下必须指定 scope" in result.stderr


def test_profile_use_confirms_overwrite(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".mcp.json").write_text(
        '{"mcpServers":{"agent-bridge":{"url":"old"}}}',
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "profile",
            "use",
            "safe-readonly",
            "--scope",
            "project",
            "--url",
            "http://127.0.0.1:8765/mcp",
        ],
        input="n\n",
    )

    assert result.exit_code == 1
    assert "已取消" in result.stderr


def test_profile_use_confirms_legacy_server_migration(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".mcp.json").write_text(
        '{"mcpServers":{"agent-capability-hub":{"url":"old"}}}',
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "profile",
            "use",
            "safe-readonly",
            "--scope",
            "project",
            "--url",
            "http://127.0.0.1:8765/mcp",
        ],
        input="n\n",
    )

    assert result.exit_code == 1
    assert "已取消" in result.stderr


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
        "url": "http://example.test/capability-profiles/safe/doc/render",
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
        "url": "http://example.test/capability-profiles/safe/pins/refresh",
        "headers": {"X-Agent-Bridge-User": "root"},
        "timeout": 10.0,
    }
