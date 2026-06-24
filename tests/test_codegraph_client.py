from __future__ import annotations

import json
import signal
import subprocess
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

import agent_bridge.knowledge_management.code_knowledge.client as client_module
from agent_bridge.knowledge_management.code_knowledge.client import CodeGraphClient


def _mock_completed_process(
    stdout: str = "", stderr: str = "", returncode: int = 0
) -> MagicMock:
    cp = MagicMock()
    cp.stdout = stdout
    cp.stderr = stderr
    cp.returncode = returncode
    return cp


def _mock_popen(monkeypatch: pytest.MonkeyPatch, completed: MagicMock) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []

    class FakeProcess:
        pid = 4321

        def __init__(self, args: list[str], **kwargs: object) -> None:
            calls.append({"args": args, "kwargs": kwargs})
            self.args = args
            self.returncode = completed.returncode

        def communicate(self, timeout: float | None = None) -> tuple[str, str]:
            return completed.stdout, completed.stderr

        def poll(self) -> int:
            return self.returncode

    monkeypatch.setattr(client_module.subprocess, "Popen", FakeProcess)
    return calls


def test_is_available_returns_true_when_cli_exists() -> None:
    client = CodeGraphClient()
    with patch("agent_bridge.knowledge_management.code_knowledge.client.subprocess.run") as mock_run:
        mock_run.return_value = _mock_completed_process(stdout="codegraph 1.0.0")
        assert client.is_available() is True
        mock_run.assert_called_once()
        # cached
        assert client.is_available() is True
        assert mock_run.call_count == 1


def test_is_available_returns_false_when_missing() -> None:
    client = CodeGraphClient()
    with patch("agent_bridge.knowledge_management.code_knowledge.client.subprocess.run", side_effect=FileNotFoundError):
        assert client.is_available() is False


def test_init_calls_correct_args(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = CodeGraphClient()
    calls = _mock_popen(monkeypatch, _mock_completed_process())

    client.init(tmp_path)

    assert calls[0]["args"] == ["codegraph", "init", "-i"]


def test_index_calls_correct_args(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = CodeGraphClient()
    calls = _mock_popen(monkeypatch, _mock_completed_process())

    client.index(tmp_path)

    assert calls[0]["args"] == ["codegraph", "index"]


def test_query_returns_parsed_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = CodeGraphClient()
    nodes = [{"name": "hello", "kind": "function", "filePath": "app.py", "score": 1.0}]
    _mock_popen(monkeypatch, _mock_completed_process(stdout=json.dumps({"results": nodes})))

    result = client.query(tmp_path, "hello")

    assert result == nodes


def test_query_limits_results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = CodeGraphClient()
    nodes = [{"name": f"fn_{i}"} for i in range(50)]
    _mock_popen(monkeypatch, _mock_completed_process(stdout=json.dumps({"results": nodes})))

    result = client.query(tmp_path, "fn", limit=10)

    assert len(result) == 10


def test_status_parses_text_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = CodeGraphClient()
    output = "Files: 42\nNodes: 1,234\nEdges: 5,678\n"
    _mock_popen(monkeypatch, _mock_completed_process(stdout=output))

    result = client.status(tmp_path)

    assert result["files"] == 42
    assert result["nodes"] == 1234
    assert result["edges"] == 5678


def test_run_raises_on_nonzero_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = CodeGraphClient()
    _mock_popen(monkeypatch, _mock_completed_process(stderr="fatal error", returncode=1))

    with pytest.raises(RuntimeError, match="fatal error"):
        client.index(tmp_path)


def test_run_starts_codegraph_in_new_process_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeProcess:
        pid = 4321
        returncode = 0

        def __init__(self, args: list[str], **kwargs: object) -> None:
            calls.append({"args": args, "kwargs": kwargs})
            self.args = args

        def communicate(self, timeout: float | None = None) -> tuple[str, str]:
            return "", ""

    monkeypatch.setattr(client_module.subprocess, "Popen", FakeProcess)

    client = CodeGraphClient()
    client.index(tmp_path)

    assert calls[0]["args"] == ["codegraph", "index"]
    assert calls[0]["kwargs"]["cwd"] == tmp_path
    assert calls[0]["kwargs"]["start_new_session"] is True


def test_run_kills_process_group_when_codegraph_times_out(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signals: list[tuple[int, signal.Signals]] = []

    class HangingProcess:
        pid = 9876
        returncode: int | None = None

        def __init__(self, args: list[str], **kwargs: object) -> None:
            self.args = args
            self._waits = 0

        def communicate(self, timeout: float | None = None) -> tuple[str, str]:
            if timeout is not None:
                raise subprocess.TimeoutExpired(self.args, timeout)
            return "", ""

        def wait(self, timeout: float | None = None) -> int:
            self._waits += 1
            if self._waits == 1:
                raise subprocess.TimeoutExpired(self.args, timeout)
            self.returncode = -signal.SIGKILL
            return self.returncode

        def poll(self) -> int | None:
            return self.returncode

    monkeypatch.setattr(client_module.subprocess, "Popen", HangingProcess)
    monkeypatch.setattr(client_module.os, "killpg", lambda pgid, sig: signals.append((pgid, sig)))

    client = CodeGraphClient(command_timeout=0.01, terminate_grace_seconds=0)

    with pytest.raises(subprocess.TimeoutExpired):
        client.index(tmp_path)

    assert signals == [(9876, signal.SIGTERM), (9876, signal.SIGKILL)]


def test_terminate_active_processes_kills_running_codegraph_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    released = threading.Event()
    signals: list[tuple[int, signal.Signals]] = []
    errors: list[Exception] = []

    class BlockingProcess:
        pid = 2468
        returncode: int | None = None

        def __init__(self, args: list[str], **kwargs: object) -> None:
            self.args = args

        def communicate(self, timeout: float | None = None) -> tuple[str, str]:
            started.set()
            released.wait(timeout=2)
            return "", ""

        def wait(self, timeout: float | None = None) -> int:
            self.returncode = -signal.SIGTERM
            return self.returncode

        def poll(self) -> int | None:
            return self.returncode

    def fake_killpg(pgid: int, sig: signal.Signals) -> None:
        signals.append((pgid, sig))
        released.set()

    monkeypatch.setattr(client_module.subprocess, "Popen", BlockingProcess)
    monkeypatch.setattr(client_module.os, "killpg", fake_killpg)

    client = CodeGraphClient(command_timeout=5, terminate_grace_seconds=0)

    def run_index() -> None:
        try:
            client.index(tmp_path)
        except RuntimeError as exc:
            errors.append(exc)

    worker = threading.Thread(target=run_index)

    worker.start()
    assert started.wait(timeout=1)

    client.terminate_active_processes()
    worker.join(timeout=1)

    assert not worker.is_alive()
    assert signals == [(2468, signal.SIGTERM)]
    assert len(errors) == 1
