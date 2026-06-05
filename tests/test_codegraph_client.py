from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from agent_bridge.codegraph.client import CodeGraphClient


def _mock_completed_process(
    stdout: str = "", stderr: str = "", returncode: int = 0
) -> MagicMock:
    cp = MagicMock()
    cp.stdout = stdout
    cp.stderr = stderr
    cp.returncode = returncode
    return cp


def test_is_available_returns_true_when_cli_exists() -> None:
    client = CodeGraphClient()
    with patch("agent_bridge.codegraph.client.subprocess.run") as mock_run:
        mock_run.return_value = _mock_completed_process(stdout="codegraph 1.0.0")
        assert client.is_available() is True
        mock_run.assert_called_once()
        # cached
        assert client.is_available() is True
        assert mock_run.call_count == 1


def test_is_available_returns_false_when_missing() -> None:
    client = CodeGraphClient()
    with patch("agent_bridge.codegraph.client.subprocess.run", side_effect=FileNotFoundError):
        assert client.is_available() is False


def test_init_calls_correct_args(tmp_path: Path) -> None:
    client = CodeGraphClient()
    with patch("agent_bridge.codegraph.client.subprocess.run") as mock_run:
        mock_run.return_value = _mock_completed_process()
        client.init(tmp_path)
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args == ["codegraph", "init", "-i"]


def test_index_calls_correct_args(tmp_path: Path) -> None:
    client = CodeGraphClient()
    with patch("agent_bridge.codegraph.client.subprocess.run") as mock_run:
        mock_run.return_value = _mock_completed_process()
        client.index(tmp_path)
        args = mock_run.call_args[0][0]
        assert args == ["codegraph", "index"]


def test_query_returns_parsed_json(tmp_path: Path) -> None:
    client = CodeGraphClient()
    nodes = [{"name": "hello", "kind": "function", "filePath": "app.py", "score": 1.0}]
    with patch("agent_bridge.codegraph.client.subprocess.run") as mock_run:
        mock_run.return_value = _mock_completed_process(stdout=json.dumps({"results": nodes}))
        result = client.query(tmp_path, "hello")
        assert result == nodes


def test_query_limits_results(tmp_path: Path) -> None:
    client = CodeGraphClient()
    nodes = [{"name": f"fn_{i}"} for i in range(50)]
    with patch("agent_bridge.codegraph.client.subprocess.run") as mock_run:
        mock_run.return_value = _mock_completed_process(stdout=json.dumps({"results": nodes}))
        result = client.query(tmp_path, "fn", limit=10)
        assert len(result) == 10


def test_status_parses_text_output(tmp_path: Path) -> None:
    client = CodeGraphClient()
    output = "Files: 42\nNodes: 1,234\nEdges: 5,678\n"
    with patch("agent_bridge.codegraph.client.subprocess.run") as mock_run:
        mock_run.return_value = _mock_completed_process(stdout=output)
        result = client.status(tmp_path)
        assert result["files"] == 42
        assert result["nodes"] == 1234
        assert result["edges"] == 5678


def test_run_raises_on_nonzero_exit(tmp_path: Path) -> None:
    client = CodeGraphClient()
    with patch("agent_bridge.codegraph.client.subprocess.run") as mock_run:
        mock_run.return_value = _mock_completed_process(stderr="fatal error", returncode=1)
        with pytest.raises(RuntimeError, match="fatal error"):
            client.index(tmp_path)
