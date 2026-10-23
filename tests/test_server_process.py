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


# -- 进程存活判断、重复启动保护与停止确认 --

import os  # noqa: E402
import signal  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402

from agent_bridge.server_runtime import server_process  # noqa: E402
from agent_bridge.server_runtime.server_process import (  # noqa: E402
    GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS,
    stop_server,
)

_MODULE = "agent_bridge.server_runtime.server_process"


def _spawn_fake_server(*, ignore_sigterm: bool) -> subprocess.Popen:
    """拉起 cmdline 含服务标记的真实子进程，可选忽略 SIGTERM（模拟收尾卡死）。"""
    handler = "signal.signal(signal.SIGTERM, signal.SIG_IGN); " if ignore_sigterm else ""
    code = (
        "import signal, sys, time; "
        f"{handler}"
        "sys.stdout.write('ready\\n'); sys.stdout.flush(); "
        "time.sleep(60)  # agent_bridge.api.app"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        start_new_session=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == b"ready"
    return process


def _write_pid(wm_paths, pid: int) -> None:
    wm_paths.run_dir.mkdir(parents=True, exist_ok=True)
    wm_paths.server_pid_path.write_text(str(pid), encoding="utf-8")


def _unhealthy(*args, **kwargs):
    raise httpx.ReadTimeout("busy")


def test_server_status_reports_alive_but_unhealthy_process_as_running(monkeypatch, wm_paths) -> None:
    process = _spawn_fake_server(ignore_sigterm=False)
    try:
        _write_pid(wm_paths, process.pid)
        monkeypatch.setattr(f"{_MODULE}.httpx.get", _unhealthy)

        status = server_status(wm_paths)

        assert status == {"running": True, "healthy": False, "pid": process.pid}
    finally:
        process.kill()
        process.wait()


def test_start_server_refuses_second_instance_when_old_process_alive(monkeypatch, wm_paths) -> None:
    """回归：/health 超时不得被当成“已停止”而再拉起一个实例并覆盖 PID 文件。"""
    process = _spawn_fake_server(ignore_sigterm=False)
    try:
        _write_pid(wm_paths, process.pid)
        monkeypatch.setattr(f"{_MODULE}.httpx.get", _unhealthy)
        real_popen = subprocess.Popen

        def no_second_server(command, *args, **kwargs):
            # macOS 的 PID 身份检查通过 ps 子进程完成，不能被启动拦截替身误伤。
            if command[:1] == ["ps"]:
                return real_popen(command, *args, **kwargs)
            pytest.fail("must not spawn a second server")

        monkeypatch.setattr(f"{_MODULE}.subprocess.Popen", no_second_server)

        with pytest.raises(RuntimeError, match="refusing to start a second instance"):
            start_server(wm_paths)

        assert wm_paths.server_pid_path.read_text(encoding="utf-8") == str(process.pid)
    finally:
        process.kill()
        process.wait()


def test_start_server_cleans_stale_pid_file_and_sets_graceful_timeout(monkeypatch, wm_paths) -> None:
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    _write_pid(wm_paths, dead.pid)
    captured = {}

    class FakeProcess:
        pid = 456

        def poll(self):
            return None

    class FakeResponse:
        status_code = 200

    def fake_popen(args, **kwargs):
        captured["args"] = args
        return FakeProcess()

    monkeypatch.setattr(f"{_MODULE}.subprocess.Popen", fake_popen)
    monkeypatch.setattr(f"{_MODULE}.httpx.get", lambda *args, **kwargs: FakeResponse())

    status = start_server(wm_paths)

    assert status == {"running": True, "pid": 456}
    assert wm_paths.server_pid_path.read_text(encoding="utf-8") == "456"
    args = captured["args"]
    index = args.index("--timeout-graceful-shutdown")
    assert args[index + 1] == str(GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS)


def test_stop_server_waits_for_exit_before_removing_pid_file(wm_paths) -> None:
    process = _spawn_fake_server(ignore_sigterm=False)
    _write_pid(wm_paths, process.pid)

    result = stop_server(wm_paths)

    assert result == {"stopped": True, "pid": process.pid, "forced": False}
    # stop_server 已经 waitpid 回收了该子进程，这里只确认它已不存在。
    assert not server_process._pid_alive(process.pid)
    assert not wm_paths.server_pid_path.exists()


def test_stop_server_escalates_to_sigkill_when_sigterm_ignored(monkeypatch, wm_paths) -> None:
    """回归：SIGTERM 失效时不得只删 PID 文件就报告已停止。"""
    process = _spawn_fake_server(ignore_sigterm=True)
    _write_pid(wm_paths, process.pid)
    monkeypatch.setattr(f"{_MODULE}._STOP_TIMEOUT_SECONDS", 0.5)

    started = time.monotonic()
    result = stop_server(wm_paths)

    assert result == {"stopped": True, "pid": process.pid, "forced": True}
    assert time.monotonic() - started < 10
    assert not server_process._pid_alive(process.pid)
    assert not wm_paths.server_pid_path.exists()


def test_stop_server_keeps_pid_file_when_process_survives_sigkill(monkeypatch, wm_paths) -> None:
    _write_pid(wm_paths, 424242)
    monkeypatch.setattr(f"{_MODULE}._server_alive", lambda pid: True)
    monkeypatch.setattr(f"{_MODULE}._pid_alive", lambda pid: True)
    monkeypatch.setattr(f"{_MODULE}._STOP_TIMEOUT_SECONDS", 0.0)
    monkeypatch.setattr(f"{_MODULE}._KILL_WAIT_SECONDS", 0.0)
    sent = []
    monkeypatch.setattr(f"{_MODULE}.os.kill", lambda pid, sig: sent.append(("kill", pid, sig)))
    monkeypatch.setattr(f"{_MODULE}.os.killpg", lambda pid, sig: sent.append(("killpg", pid, sig)))

    with pytest.raises(RuntimeError, match="did not exit after SIGKILL"):
        stop_server(wm_paths)

    assert sent == [("kill", 424242, signal.SIGTERM), ("killpg", 424242, signal.SIGKILL)]
    assert wm_paths.server_pid_path.exists()


def test_stop_server_does_not_signal_reused_pid(monkeypatch, wm_paths) -> None:
    """PID 文件残留且 PID 已被其他进程复用时，只清理 PID 文件，不发信号。"""
    _write_pid(wm_paths, os.getpid())
    monkeypatch.setattr(
        f"{_MODULE}.os.kill",
        lambda pid, sig: pytest.fail("must not signal an unrelated process") if sig else None,
    )

    result = stop_server(wm_paths)

    assert result == {"stopped": True, "pid": os.getpid(), "forced": False}
    assert not wm_paths.server_pid_path.exists()
    assert server_process._is_server_process(os.getpid()) is False
