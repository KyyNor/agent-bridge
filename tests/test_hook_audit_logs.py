from __future__ import annotations

import json

from agent_bridge.app.service import AgentBridgeService
from agent_bridge.capability_hub.models import SourceType


class FakeWorkerService:
    def __init__(self, *, status: str = "ok", stdout: str = '{"continue":true}', stderr: str = "", exit_code: int = 0):
        self.status = status
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code

    def handle_hook(self, block, *, action, payload, event_name, matcher, timeout_seconds):
        return {
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "status": self.status,
        }


def _service(wm_paths):
    service = AgentBridgeService.create(wm_paths, admins={"root"})
    service.init_system()
    service.governance.upsert_profile("root", "dev", "Dev", "", "active")
    service.memory.create_block("root", "dev-memory", "Dev Memory", "")
    service.memory.set_profile_binding("root", "dev", "dev-memory", enabled=True)
    return service


def test_memory_hook_writes_compatible_tool_call_log(wm_paths) -> None:
    service = _service(wm_paths)
    fake_worker = FakeWorkerService()
    service.memory.worker_service = fake_worker
    service.memory.hooks.worker_service = fake_worker

    service.memory.hooks.handle_claude_code_hook(
        actor="root",
        profile_key="dev",
        action="observation",
        event_name="PostToolUse",
        matcher="*",
        payload={"tool_name": "Read"},
        timeout_seconds=120,
    )

    logs = service.governance.list_logs(actor="root", source_type=SourceType.hook.value)
    assert len(logs) == 1
    log = logs[0]
    assert log["entrypoint"] == "memory_hook_claude_code"
    assert log["source_key"] == "claude_code"
    assert log["tool_name"] == "observation"
    assert log["status"] == "success"
    assert json.loads(log["request_json"]) == {
        "action": "observation",
        "event_name": "PostToolUse",
        "matcher": "*",
        "payload": {"tool_name": "Read"},
        "timeout_seconds": 120,
        "source": "claude-code",
    }
    assert json.loads(log["response_json"]) == {
        "stdout": '{"continue":true}',
        "stderr": "",
        "exit_code": 0,
        "status": "ok",
    }


def test_memory_hook_logs_worker_failures_as_error_rows(wm_paths) -> None:
    service = _service(wm_paths)
    fake_worker = FakeWorkerService(status="worker_error", stderr="worker unavailable")
    service.memory.worker_service = fake_worker
    service.memory.hooks.worker_service = fake_worker

    service.memory.hooks.handle_claude_code_hook(
        actor="root",
        profile_key="dev",
        action="context",
        event_name="SessionStart",
        matcher="startup|clear|compact",
        payload={"source": "startup"},
        timeout_seconds=60,
    )

    logs = service.governance.list_logs(actor="root", source_type=SourceType.hook.value, status="error")
    assert len(logs) == 1
    log = logs[0]
    assert log["tool_name"] == "context"
    assert log["error_message"] == "worker unavailable"
