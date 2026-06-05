from __future__ import annotations

import json
from pathlib import Path

import httpx
from typer.testing import CliRunner

from agent_bridge.client import AgentBridgeClient
from agent_bridge.cli import app


runner = CliRunner()


def test_kb_list_calls_client(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def list_kbs(self):
            calls.append("list_kbs")
            return [{"slug": "frontend-docs", "role": "contributor"}]

    monkeypatch.setattr("agent_bridge.cli.AgentBridgeClient.from_config", lambda: FakeClient())
    result = runner.invoke(app, ["kb", "list"])
    assert result.exit_code == 0
    assert "frontend-docs" in result.stdout
    assert calls == ["list_kbs"]


def test_add_command_sends_file_and_kbs(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"one")
    captured = {}

    class FakeClient:
        def add_document(self, source, kb_slugs, later):
            captured["source"] = source
            captured["kb_slugs"] = kb_slugs
            captured["later"] = later
            return {"slug": "guide", "current_version_no": 1}

    monkeypatch.setattr("agent_bridge.cli.AgentBridgeClient.from_config", lambda: FakeClient())
    result = runner.invoke(app, ["add", str(source), "--kb", "frontend-docs", "--later"])
    assert result.exit_code == 0
    assert "guide" in result.stdout
    assert captured == {"source": source, "kb_slugs": ["frontend-docs"], "later": True}


def test_sync_command_prints_processed_count(monkeypatch) -> None:
    class FakeClient:
        def sync(self, all_users, backend=None):
            return {"processed": 2}

    monkeypatch.setattr("agent_bridge.cli.AgentBridgeClient.from_config", lambda: FakeClient())
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0
    assert "processed: 2" in result.stdout


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

    monkeypatch.setattr("agent_bridge.cli.AgentBridgeClient.from_config", lambda: FakeClient())
    result = runner.invoke(app, ["server", "init"])
    assert result.exit_code == 0
    assert "initialized" in result.stdout
    assert calls == ["init_system"]


def test_status_reports_service_unavailable_cleanly(monkeypatch) -> None:
    class FakeClient:
        def status(self, backend=None):
            raise httpx.ConnectError("boom")

    monkeypatch.setattr("agent_bridge.cli.AgentBridgeClient.from_config", lambda: FakeClient())
    result = runner.invoke(app, ["status"])
    output = f"{result.stdout}{result.stderr}"
    assert result.exit_code == 1
    assert "service unavailable" in result.stderr or "boom" in result.stderr
    assert "Traceback" not in output


def test_add_missing_file_reports_clean_cli_error() -> None:
    result = runner.invoke(app, ["add", "missing.pdf", "--kb", "frontend-docs"])
    output = f"{result.stdout}{result.stderr}"
    assert result.exit_code != 0
    assert "Traceback" not in output


def test_server_status_command(monkeypatch) -> None:
    monkeypatch.setattr("agent_bridge.cli.server_status", lambda: {"running": True, "pid": 123})
    result = runner.invoke(app, ["server", "status"])
    assert result.exit_code == 0
    assert "running" in result.stdout
    assert "123" in result.stdout


def test_server_status_accepts_root_option(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    def fake_status(paths):
        captured["root"] = paths.root
        return {"running": True, "pid": 123}

    monkeypatch.setattr("agent_bridge.cli.server_status", fake_status)
    result = runner.invoke(app, ["server", "status", "--root", str(tmp_path)])

    assert result.exit_code == 0
    assert captured == {"root": tmp_path}


def test_server_start_reports_errors_cleanly(monkeypatch) -> None:
    def fail_start():
        raise OSError("permission denied")

    monkeypatch.setattr("agent_bridge.cli.start_server", fail_start)
    result = runner.invoke(app, ["server", "start"])
    output = f"{result.stdout}{result.stderr}"
    assert result.exit_code == 1
    assert "server error" in result.stderr
    assert "permission denied" in result.stderr
    assert "Traceback" not in output


def test_purge_without_yes_exits_without_calling_client(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def purge_document(self, doc_slug, confirm):
            calls.append((doc_slug, confirm))
            return {"slug": doc_slug, "status": "purged"}

    monkeypatch.setattr("agent_bridge.cli.AgentBridgeClient.from_config", lambda: FakeClient())
    result = runner.invoke(app, ["purge", "guide"])
    output = f"{result.stdout}{result.stderr}"
    assert result.exit_code == 1
    assert "requires --yes" in output
    assert "Traceback" not in output
    assert calls == []


def test_purge_with_yes_calls_client(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def purge_document(self, doc_slug, confirm):
            calls.append((doc_slug, confirm))
            return {"slug": doc_slug, "status": "purged"}

    monkeypatch.setattr("agent_bridge.cli.AgentBridgeClient.from_config", lambda: FakeClient())
    result = runner.invoke(app, ["purge", "guide", "--yes"])
    assert result.exit_code == 0
    assert "purged" in result.stdout
    assert calls == [("guide", True)]


def test_list_backends(monkeypatch) -> None:
    class FakeClient:
        def list_backends(self):
            return [{"slug": "local-gpt", "type": "openai"}]

    monkeypatch.setattr("agent_bridge.cli.AgentBridgeClient.from_config", lambda: FakeClient())
    result = runner.invoke(app, ["backends"])
    assert result.exit_code == 0
    assert "local-gpt" in result.stdout
    assert "openai" in result.stdout


def test_search_command_calls_client(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def search(self, kb_slug, question, backend=None, top_k=6):
            calls.append({"kb": kb_slug, "q": question, "backend": backend, "top_k": top_k})
            return {
                "results": [
                    {
                        "document_name": "auth.md",
                        "similarity": 0.93,
                        "content": "OAuth2 flow description",
                    }
                ]
            }

    monkeypatch.setattr("agent_bridge.cli.AgentBridgeClient.from_config", lambda: FakeClient())
    result = runner.invoke(app, ["search", "how does auth work", "--kb", "frontend-docs"])
    assert result.exit_code == 0
    assert "auth.md" in result.stdout
    assert calls == [{"kb": "frontend-docs", "q": "how does auth work", "backend": None, "top_k": 6}]


def test_search_command_no_results(monkeypatch) -> None:
    class FakeClient:
        def search(self, kb_slug, question, backend=None, top_k=6):
            return {"results": []}

    monkeypatch.setattr("agent_bridge.cli.AgentBridgeClient.from_config", lambda: FakeClient())
    result = runner.invoke(app, ["search", "nonexistent", "--kb", "test-kb"])
    assert result.exit_code == 0
    assert "no results" in result.stdout


def test_ask_command_calls_client(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def ask(self, kb_slug, question, backend=None, session_id=None):
            calls.append({"kb": kb_slug, "q": question, "backend": backend, "session_id": session_id})
            return {"answer": "OAuth2 uses tokens.", "session_id": "abc123"}

    monkeypatch.setattr("agent_bridge.cli.AgentBridgeClient.from_config", lambda: FakeClient())
    result = runner.invoke(app, ["ask", "how does auth work", "--kb", "frontend-docs"])
    assert result.exit_code == 0
    assert "OAuth2 uses tokens." in result.stdout
    assert "session: abc123" in result.stdout
    assert calls == [{"kb": "frontend-docs", "q": "how does auth work", "backend": None, "session_id": None}]


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


def test_metamcp_profile_create_calls_client(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def upsert_profile(self, profile_key, name, description, status):
            calls.append((profile_key, name, description, status))
            return {"profile_key": profile_key, "name": name}

    monkeypatch.setattr("agent_bridge.cli.AgentBridgeClient.from_config", lambda: FakeClient())
    result = runner.invoke(app, ["metamcp", "profile", "create", "safe-readonly", "--name", "安全只读"])

    assert result.exit_code == 0
    assert "safe-readonly" in result.stdout
    assert calls == [("safe-readonly", "安全只读", "", "active")]


def test_metamcp_profile_rules_calls_client(monkeypatch) -> None:
    captured = {}

    class FakeClient:
        def replace_profile_rules(self, profile_key, rules):
            captured["profile_key"] = profile_key
            captured["rules"] = rules
            return {"profile_key": profile_key, "rules": rules}

    monkeypatch.setattr("agent_bridge.cli.AgentBridgeClient.from_config", lambda: FakeClient())
    result = runner.invoke(
        app,
        ["metamcp", "profile", "rules", "safe-readonly", "--allow", "mysql", "--deny", "hive"],
    )

    assert result.exit_code == 0
    assert captured["profile_key"] == "safe-readonly"
    assert captured["rules"] == [
        {"source_type": "mcp_service", "source_key": "mysql", "effect": "allow"},
        {"source_type": "mcp_service", "source_key": "hive", "effect": "deny"},
    ]


def test_metamcp_add_writes_project_config(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app,
        [
            "metamcp",
            "add",
            "--scope",
            "project",
            "--url",
            "http://127.0.0.1:8765/mcp",
            "--profile",
            "safe-readonly",
        ],
    )

    config = tmp_path / ".mcp.json"
    assert result.exit_code == 0
    assert config.exists()
    assert "safe-readonly" in config.read_text(encoding="utf-8")


def test_metamcp_add_preserves_existing_servers(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    config = tmp_path / ".mcp.json"
    config.write_text(
        json.dumps({"mcpServers": {"existing": {"command": "node"}}}),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "metamcp",
            "add",
            "--scope",
            "project",
            "--url",
            "http://127.0.0.1:8765/mcp",
            "--profile",
            "safe-readonly",
        ],
    )

    data = json.loads(config.read_text(encoding="utf-8"))
    assert result.exit_code == 0
    assert "existing" in data["mcpServers"]
    assert "agent-capability-hub" in data["mcpServers"]


def test_metamcp_add_prompts_for_scope_when_missing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
    monkeypatch.setattr("agent_bridge.cli._stdin_is_interactive", lambda: True)

    result = runner.invoke(
        app,
        [
            "metamcp",
            "add",
            "--url",
            "http://127.0.0.1:8765/mcp",
            "--profile",
            "safe-readonly",
        ],
        input="project\n",
    )

    assert result.exit_code == 0
    assert (tmp_path / ".mcp.json").exists()
    assert "written:" in result.output


def test_metamcp_add_requires_scope_in_non_interactive_mode(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("agent_bridge.cli._stdin_is_interactive", lambda: False)

    result = runner.invoke(
        app,
        ["metamcp", "add", "--url", "http://127.0.0.1:8765/mcp", "--profile", "safe-readonly"],
    )

    assert result.exit_code == 1
    assert "scope is required in non-interactive mode" in result.stderr


def test_metamcp_add_confirms_overwrite(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".mcp.json").write_text(
        '{"mcpServers":{"agent-capability-hub":{"url":"old"}}}',
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "metamcp",
            "add",
            "--scope",
            "project",
            "--url",
            "http://127.0.0.1:8765/mcp",
            "--profile",
            "safe-readonly",
        ],
        input="n\n",
    )

    assert result.exit_code == 1
    assert "aborted" in result.stderr
