from __future__ import annotations

from pathlib import Path

import httpx
from typer.testing import CliRunner

from wiki_manager.client import WikiManagerClient
from wiki_manager.cli import app


runner = CliRunner()


def test_kb_list_calls_client(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def list_kbs(self):
            calls.append("list_kbs")
            return [{"slug": "frontend-docs", "role": "contributor"}]

    monkeypatch.setattr("wiki_manager.cli.WikiManagerClient.from_config", lambda: FakeClient())
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

    monkeypatch.setattr("wiki_manager.cli.WikiManagerClient.from_config", lambda: FakeClient())
    result = runner.invoke(app, ["add", str(source), "--kb", "frontend-docs", "--later"])
    assert result.exit_code == 0
    assert "guide" in result.stdout
    assert captured == {"source": source, "kb_slugs": ["frontend-docs"], "later": True}


def test_sync_command_prints_processed_count(monkeypatch) -> None:
    class FakeClient:
        def sync(self, all_users):
            return {"processed": 2}

    monkeypatch.setattr("wiki_manager.cli.WikiManagerClient.from_config", lambda: FakeClient())
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

    monkeypatch.setattr("wiki_manager.client.httpx.post", fake_post)
    WikiManagerClient("http://example.test/", "root").init_system()
    assert captured == {
        "url": "http://example.test/admin/init",
        "headers": {"X-Wiki-User": "root"},
        "timeout": 10.0,
    }


def test_client_purge_document_sends_confirmation(monkeypatch) -> None:
    captured = {}

    def fake_post(url, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        return httpx.Response(200, json={"slug": "guide", "status": "purged"})

    monkeypatch.setattr("wiki_manager.client.httpx.post", fake_post)
    result = WikiManagerClient("http://example.test/", "root").purge_document("guide", confirm=True)
    assert result == {"slug": "guide", "status": "purged"}
    assert captured == {
        "url": "http://example.test/docs/guide/purge",
        "json": {"confirm": True},
        "headers": {"X-Wiki-User": "root"},
        "timeout": 10.0,
    }


def test_server_init_calls_client(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def init_system(self):
            calls.append("init_system")

    monkeypatch.setattr("wiki_manager.cli.WikiManagerClient.from_config", lambda: FakeClient())
    result = runner.invoke(app, ["server", "init"])
    assert result.exit_code == 0
    assert "initialized" in result.stdout
    assert calls == ["init_system"]


def test_status_reports_service_unavailable_cleanly(monkeypatch) -> None:
    class FakeClient:
        def status(self):
            raise httpx.ConnectError("boom")

    monkeypatch.setattr("wiki_manager.cli.WikiManagerClient.from_config", lambda: FakeClient())
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
    monkeypatch.setattr("wiki_manager.cli.server_status", lambda: {"running": True, "pid": 123})
    result = runner.invoke(app, ["server", "status"])
    assert result.exit_code == 0
    assert "running" in result.stdout
    assert "123" in result.stdout


def test_server_start_reports_errors_cleanly(monkeypatch) -> None:
    def fail_start():
        raise OSError("permission denied")

    monkeypatch.setattr("wiki_manager.cli.start_server", fail_start)
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

    monkeypatch.setattr("wiki_manager.cli.WikiManagerClient.from_config", lambda: FakeClient())
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

    monkeypatch.setattr("wiki_manager.cli.WikiManagerClient.from_config", lambda: FakeClient())
    result = runner.invoke(app, ["purge", "guide", "--yes"])
    assert result.exit_code == 0
    assert "purged" in result.stdout
    assert calls == [("guide", True)]
