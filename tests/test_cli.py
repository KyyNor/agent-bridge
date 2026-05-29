from __future__ import annotations

from pathlib import Path

import httpx
from typer.testing import CliRunner

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
