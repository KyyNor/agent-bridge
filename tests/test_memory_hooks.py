from __future__ import annotations

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
