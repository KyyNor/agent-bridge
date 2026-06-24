from __future__ import annotations

import json
import os
import signal
import subprocess

from agent_bridge.app.service import AgentBridgeService
from agent_bridge.memory_management.claude_mem.worker import ClaudeMemWorkerService
from agent_bridge.memory_management.models import NOOP_HOOK_STDOUT


class FakeWorkerService:
    def __init__(self):
        self.calls = []

    def handle_hook(self, block, *, action, payload, event_name, matcher, timeout_seconds):
        self.calls.append(
            {
                "block_key": block["block_key"],
                "action": action,
                "payload": payload,
                "event_name": event_name,
                "matcher": matcher,
                "timeout_seconds": timeout_seconds,
            }
        )
        return {"stdout": '{"continue":true}', "stderr": "", "exit_code": 0, "status": "ok"}


def _service(wm_paths):
    service = AgentBridgeService.create(wm_paths, admins={"root"})
    service.init_system()
    service.governance.upsert_profile("root", "dev", "Dev", "", "active")
    service.memory.create_block("root", "dev-memory", "Dev Memory", "")
    service.memory.set_profile_binding("root", "dev", "dev-memory", enabled=True)
    return service


def test_hook_service_resolves_profile_binding_and_calls_worker(wm_paths):
    service = _service(wm_paths)
    fake_worker = FakeWorkerService()
    service.memory.worker_service = fake_worker
    service.memory.hooks.worker_service = fake_worker

    result = service.memory.hooks.handle_claude_code_hook(
        actor="root",
        profile_key="dev",
        action="observation",
        event_name="PostToolUse",
        matcher="*",
        payload={"tool_name": "Read"},
        timeout_seconds=120,
    )

    assert result == {"stdout": '{"continue":true}', "stderr": "", "exit_code": 0, "status": "ok"}
    assert fake_worker.calls == [
        {
            "block_key": "dev-memory",
            "action": "observation",
            "payload": {"tool_name": "Read"},
            "event_name": "PostToolUse",
            "matcher": "*",
            "timeout_seconds": 120,
        }
    ]


def test_hook_service_returns_noop_when_profile_unbound(wm_paths):
    service = AgentBridgeService.create(wm_paths, admins={"root"})
    service.init_system()
    service.governance.upsert_profile("root", "dev", "Dev", "", "active")

    result = service.memory.hooks.handle_claude_code_hook(
        actor="root",
        profile_key="dev",
        action="context",
        event_name="SessionStart",
        matcher="startup|clear|compact",
        payload={"source": "startup"},
        timeout_seconds=60,
    )

    assert result == {"stdout": NOOP_HOOK_STDOUT, "stderr": "", "exit_code": 0, "status": "not_configured"}


def test_hook_service_rejects_unknown_action(wm_paths):
    service = _service(wm_paths)

    result = service.memory.hooks.handle_claude_code_hook(
        actor="root",
        profile_key="dev",
        action="made-up",
        event_name="Stop",
        matcher=None,
        payload={},
        timeout_seconds=60,
    )

    assert result["exit_code"] == 0
    assert result["status"] == "unsupported_action"
    assert result["stdout"] == NOOP_HOOK_STDOUT


def test_worker_executes_original_claude_mem_hook_command_with_block_data_dir(wm_paths, tmp_path, monkeypatch):
    plugin_dir = tmp_path / "claude-mem"
    scripts = plugin_dir / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "bun-runner.js").write_text("", encoding="utf-8")
    (scripts / "worker-service.cjs").write_text("", encoding="utf-8")
    (scripts / "version-check.js").write_text("", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_MEM_PLUGIN_ROOT", str(plugin_dir))
    calls = []

    def fake_run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        return subprocess.CompletedProcess(command, 0, stdout='{"continue":true}\n', stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(ClaudeMemWorkerService, "_ensure_worker", lambda self, block, **kwargs: "http://127.0.0.1:37777")
    block = {
        "block_key": "dev-memory",
        "data_dir": str(wm_paths.data_dir / "claude-mem" / "blocks" / "dev-memory"),
    }

    result = ClaudeMemWorkerService(paths=wm_paths).handle_hook(
        block,
        action="observation",
        payload={"tool_name": "Read"},
        event_name="PostToolUse",
        matcher="*",
        timeout_seconds=120,
    )

    assert result == {"stdout": '{"continue":true}', "stderr": "", "exit_code": 0, "status": "ok"}
    assert calls[0]["command"] == [
        "node",
        str(scripts / "bun-runner.js"),
        str(scripts / "worker-service.cjs"),
        "hook",
        "claude-code",
        "observation",
    ]
    assert calls[0]["env"]["CLAUDE_MEM_DATA_DIR"] == block["data_dir"]
    assert calls[0]["input"] == '{"tool_name": "Read", "hook_event_name": "PostToolUse", "matcher": "*"}'


def test_worker_start_hook_launches_managed_worker_without_original_start_wrapper(wm_paths, tmp_path, monkeypatch):
    plugin_dir = tmp_path / "claude-mem"
    scripts = plugin_dir / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "bun-runner.js").write_text("", encoding="utf-8")
    (scripts / "worker-service.cjs").write_text("", encoding="utf-8")
    (scripts / "version-check.js").write_text("", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_MEM_PLUGIN_ROOT", str(plugin_dir))
    worker_started = False
    popen_calls = []

    class FakeProcess:
        pid = 4242
        returncode = None

        def poll(self):
            return None

    def fake_popen(command, **kwargs):
        nonlocal worker_started
        worker_started = True
        popen_calls.append({"command": command, **kwargs})
        return FakeProcess()

    def fail_run(*args, **kwargs):
        raise AssertionError("start hook should not call the original claude-mem start wrapper")

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(subprocess, "run", fail_run)
    monkeypatch.setattr(ClaudeMemWorkerService, "_bun_command", lambda self: "/usr/local/bin/bun")
    monkeypatch.setattr(ClaudeMemWorkerService, "_worker_ready", lambda self, base_url: worker_started)
    block = {
        "block_key": "dev-memory",
        "data_dir": str(wm_paths.data_dir / "claude-mem" / "blocks" / "dev-memory"),
    }

    result = ClaudeMemWorkerService(paths=wm_paths).handle_hook(
        block,
        action="start",
        payload={"source": "startup"},
        event_name="SessionStart",
        matcher="startup|clear|compact",
        timeout_seconds=60,
    )

    assert result == {"stdout": NOOP_HOOK_STDOUT, "stderr": "", "exit_code": 0, "status": "ok"}
    assert popen_calls[0]["command"] == ["/usr/local/bin/bun", str(scripts / "worker-service.cjs")]
    assert popen_calls[0]["cwd"] == plugin_dir
    assert popen_calls[0]["env"]["CLAUDE_MEM_DATA_DIR"] == block["data_dir"]
    assert popen_calls[0]["env"]["CLAUDE_MEM_PLUGIN_ROOT"] == str(plugin_dir)
    assert (wm_paths.run_dir / "claude-mem-workers" / "dev-memory.json").exists()


def test_worker_start_allocates_ports_from_claude_mem_pool(wm_paths, tmp_path, monkeypatch):
    plugin_dir = tmp_path / "claude-mem"
    scripts = plugin_dir / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "bun-runner.js").write_text("", encoding="utf-8")
    (scripts / "worker-service.cjs").write_text("", encoding="utf-8")
    (scripts / "version-check.js").write_text("", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_MEM_PLUGIN_ROOT", str(plugin_dir))
    started_ports = []

    class FakeProcess:
        returncode = None

        def __init__(self, pid):
            self.pid = pid

        def poll(self):
            return None

    def fake_popen(command, **kwargs):
        port = int(kwargs["env"]["CLAUDE_MEM_WORKER_PORT"])
        started_ports.append(port)
        return FakeProcess(5000 + len(started_ports))

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(ClaudeMemWorkerService, "_bun_command", lambda self: "/usr/local/bin/bun")
    monkeypatch.setattr(ClaudeMemWorkerService, "_wait_until_ready", lambda self, base_url, **kwargs: True)
    monkeypatch.setattr(ClaudeMemWorkerService, "_pid_alive", lambda self, pid: pid >= 5001)
    monkeypatch.setattr(ClaudeMemWorkerService, "_port_in_use", lambda self, port: port in started_ports)
    monkeypatch.setattr(ClaudeMemWorkerService, "_worker_ready", lambda self, base_url: False)
    service = ClaudeMemWorkerService(paths=wm_paths)

    first = service._ensure_worker(
        {"block_key": "first-memory", "data_dir": str(wm_paths.data_dir / "claude-mem" / "blocks" / "first-memory")}
    )
    second = service._ensure_worker(
        {"block_key": "second-memory", "data_dir": str(wm_paths.data_dir / "claude-mem" / "blocks" / "second-memory")}
    )

    assert first == "http://127.0.0.1:48100"
    assert second == "http://127.0.0.1:48101"
    assert started_ports == [48100, 48101]


def test_worker_port_pool_evicts_least_recently_used_worker_when_full(wm_paths, monkeypatch):
    state_dir = wm_paths.run_dir / "claude-mem-workers"
    state_dir.mkdir(parents=True)
    alive_pids = set(range(6000, 6020))
    port_by_pid = {}
    for index in range(20):
        pid = 6000 + index
        port = 48100 + index
        port_by_pid[pid] = port
        (state_dir / f"memory-{index}.json").write_text(
            json.dumps(
                {
                    "pid": pid,
                    "port": port,
                    "base_url": f"http://127.0.0.1:{port}",
                    "started_at": 1000 + index,
                    "last_accessed_at": 1000 + index,
                }
            ),
            encoding="utf-8",
        )
    signals = []

    def fake_signal(pid, sig):
        signals.append((pid, sig))
        if sig == signal.SIGTERM:
            alive_pids.discard(pid)

    monkeypatch.setattr(ClaudeMemWorkerService, "_pid_alive", lambda self, pid: pid in alive_pids)
    monkeypatch.setattr(ClaudeMemWorkerService, "_signal_worker", lambda self, pid, sig: fake_signal(pid, sig))
    monkeypatch.setattr(
        ClaudeMemWorkerService,
        "_port_in_use",
        lambda self, port: any(port_by_pid.get(pid) == port for pid in alive_pids),
    )

    port = ClaudeMemWorkerService(paths=wm_paths)._available_worker_port(
        {"block_key": "new-memory", "data_dir": str(wm_paths.data_dir / "claude-mem" / "blocks" / "new-memory")},
        start_port=48100,
    )

    assert port == 48100
    assert (6000, signal.SIGTERM) in signals
    assert not (state_dir / "memory-0.json").exists()


def test_worker_stop_all_workers_terminates_state_pids_and_removes_state_files(wm_paths, monkeypatch):
    state_dir = wm_paths.run_dir / "claude-mem-workers"
    state_dir.mkdir(parents=True)
    state_path = state_dir / "dev-memory.json"
    state_path.write_text(json.dumps({"pid": 4242, "base_url": "http://127.0.0.1:37742"}), encoding="utf-8")
    calls = []

    def fake_kill(pid, sig):
        calls.append((pid, sig))
        if sig == 0:
            return None
        return None

    monkeypatch.setattr(os, "kill", fake_kill)

    result = ClaudeMemWorkerService(paths=wm_paths).stop_all_workers(grace_seconds=0)

    assert result["stopped"] == 1
    assert (4242, signal.SIGTERM) in calls
    assert (4242, signal.SIGKILL) in calls
    assert not state_path.exists()


def test_worker_stop_all_workers_uses_block_pid_files_when_bridge_state_is_missing(wm_paths, monkeypatch):
    block_dir = wm_paths.data_dir / "claude-mem" / "blocks" / "dev-memory"
    block_dir.mkdir(parents=True)
    worker_pid_path = block_dir / "worker.pid"
    supervisor_path = block_dir / "supervisor.json"
    worker_pid_path.write_text(json.dumps({"pid": 4242}), encoding="utf-8")
    supervisor_path.write_text(
        json.dumps({"processes": {"worker": {"pid": 4242}, "chroma-mcp": {"pid": 4243}}}),
        encoding="utf-8",
    )
    calls = []

    def fake_kill(pid, sig):
        calls.append(("pid", pid, sig))
        if sig == 0:
            return None
        return None

    monkeypatch.setattr(os, "kill", fake_kill)

    result = ClaudeMemWorkerService(paths=wm_paths).stop_all_workers(grace_seconds=0)

    assert result["stopped"] == 2
    assert ("pid", 4242, signal.SIGTERM) in calls
    assert ("pid", 4243, signal.SIGTERM) in calls
    assert not worker_pid_path.exists()
    assert json.loads(supervisor_path.read_text(encoding="utf-8")) == {"processes": {}}


def test_worker_executes_original_claude_mem_version_check_command(wm_paths, tmp_path, monkeypatch):
    plugin_dir = tmp_path / "claude-mem"
    scripts = plugin_dir / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "bun-runner.js").write_text("", encoding="utf-8")
    (scripts / "worker-service.cjs").write_text("", encoding="utf-8")
    (scripts / "version-check.js").write_text("", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_MEM_PLUGIN_ROOT", str(plugin_dir))
    calls = []

    def fake_run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = ClaudeMemWorkerService(paths=wm_paths).handle_hook(
        {"block_key": "dev-memory", "data_dir": "/memory/dev"},
        action="version-check",
        payload={},
        event_name=None,
        matcher=None,
        timeout_seconds=30,
    )

    assert result["stdout"] == NOOP_HOOK_STDOUT
    assert result["exit_code"] == 0
    assert calls[0]["command"] == ["node", str(scripts / "version-check.js")]


def test_worker_clones_managed_claude_mem_plugin_and_runs_bun_install(wm_paths, monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        if command[:2] == ["git", "clone"]:
            repo_dir = wm_paths.plugins_dir / "claude-mem"
            scripts = repo_dir / "plugin" / "scripts"
            scripts.mkdir(parents=True)
            (repo_dir / ".git").mkdir()
            (scripts / "bun-runner.js").write_text("", encoding="utf-8")
            (scripts / "worker-service.cjs").write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.delenv("CLAUDE_MEM_PLUGIN_ROOT", raising=False)
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = ClaudeMemWorkerService(paths=wm_paths).ensure_plugin("https://example.test/claude-mem.git")

    assert result["status"] == "cloned"
    assert result["plugin_dir"] == str(wm_paths.plugins_dir / "claude-mem" / "plugin")
    assert calls[0]["command"] == ["git", "clone", "https://example.test/claude-mem.git", str(wm_paths.plugins_dir / "claude-mem")]
    assert calls[1]["command"] == ["bun", "install"]
    assert calls[1]["cwd"] == str(wm_paths.plugins_dir / "claude-mem" / "plugin")


def test_worker_returns_noop_when_claude_mem_plugin_unavailable(wm_paths, tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_MEM_PLUGIN_ROOT", raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "empty-claude"))

    result = ClaudeMemWorkerService(paths=wm_paths).handle_hook(
        {"block_key": "dev-memory", "data_dir": "/memory/dev"},
        action="context",
        payload={},
        event_name="SessionStart",
        matcher=None,
        timeout_seconds=30,
    )

    assert result["stdout"] == NOOP_HOOK_STDOUT
    assert result["exit_code"] == 0
    assert result["status"] == "claude_mem_not_installed"
