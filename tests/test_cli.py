from __future__ import annotations

import json
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
    assert "agent-capability-hub" in data["mcpServers"]


def test_profile_use_prompts_for_scope_when_missing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
    monkeypatch.setattr("agent_bridge.cli.app._stdin_is_interactive", lambda: True)
    monkeypatch.setattr("questionary.select", lambda *args, **kwargs: type("Prompt", (), {"ask": lambda self: "project"})())

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
