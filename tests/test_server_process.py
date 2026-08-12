from __future__ import annotations

import httpx
import pytest

from agent_bridge.core.config import ROOT_ENV_VAR
from agent_bridge.server_runtime.server_process import server_status, start_server


def test_server_status_reports_invalid_pid_file(wm_paths) -> None:
    wm_paths.run_dir.mkdir(parents=True)
    wm_paths.server_pid_path.write_text("not-a-pid", encoding="utf-8")

    with pytest.raises(RuntimeError, match="invalid pid"):
        server_status(wm_paths)


def test_start_server_passes_root_to_child_environment(monkeypatch, wm_paths) -> None:
    captured = {}

    class FakeProcess:
        pid = 123

        def poll(self):
            return None

    def fake_popen(*args, **kwargs):
        captured["env"] = kwargs["env"]
        return FakeProcess()

    class FakeResponse:
        status_code = 200

    monkeypatch.setattr("agent_bridge.server_runtime.server_process.subprocess.Popen", fake_popen)
    monkeypatch.setattr("agent_bridge.server_runtime.server_process.httpx.get", lambda *args, **kwargs: FakeResponse())

    status = start_server(wm_paths)

    assert status == {"running": True, "pid": 123}
    assert captured["env"][ROOT_ENV_VAR] == str(wm_paths.root)


def test_start_server_waits_up_to_fifteen_seconds_for_health(monkeypatch, wm_paths) -> None:
    class FakeProcess:
        pid = 123

        def poll(self):
            return None

    monotonic_values = iter((0.0, 0.0, 15.0))
    monkeypatch.setattr("agent_bridge.server_runtime.server_process.subprocess.Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(
        "agent_bridge.server_runtime.server_process.httpx.get",
        lambda *args, **kwargs: (_ for _ in ()).throw(httpx.ConnectError("not ready")),
    )
    monkeypatch.setattr("agent_bridge.server_runtime.server_process.time.monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr("agent_bridge.server_runtime.server_process.time.sleep", lambda _: None)

    with pytest.raises(RuntimeError, match="within 15 seconds"):
        start_server(wm_paths)
