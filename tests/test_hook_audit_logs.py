from __future__ import annotations

import json

import pytest

from agent_bridge.app.service import AgentBridgeService
from agent_bridge.capability_hub.models import SourceType
from agent_bridge.hooks.claude_code import audit_claude_code_hook_call
from agent_bridge.knowledge_management.memory.models import NOOP_HOOK_STDOUT


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


class FakeGovernance:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def log_tool_call(self, **kwargs) -> None:
        self.calls.append(kwargs)


def _service(wm_paths):
    service = AgentBridgeService.create(wm_paths, admins={"root"})
    service.init_system()
    service.governance.upsert_profile("root", "dev", "Dev", "", "active")
    service.memory.create_block("root", "dev-memory", "Dev Memory", "")
    service.memory.set_profile_binding("root", "dev", "dev-memory", enabled=True)
    return service


def _audit(*, governance: FakeGovernance, result: dict[str, object], exception: Exception | None = None) -> dict:
    audit_claude_code_hook_call(
        governance,
        actor="root",
        profile_key="dev",
        entrypoint="memory_hook_claude_code",
        action="session-start",
        event_name="SessionStart",
        matcher="startup|resume|clear|compact",
        payload={"source": "startup"},
        timeout_seconds=60,
        result=result,
        duration_ms=12,
        exception=exception,
    )
    return governance.calls[0]


def test_audit_claude_code_hook_call_records_standard_hook_envelopes() -> None:
    captured = _audit(
        governance=FakeGovernance(),
        result={
            "stdout": '{"hookSpecificOutput":{}}',
            "stderr": "",
            "exit_code": 0,
            "status": "ok",
        },
    )

    assert captured["request"] == {
        "action": "session-start",
        "event_name": "SessionStart",
        "matcher": "startup|resume|clear|compact",
        "payload": {"source": "startup"},
        "timeout_seconds": 60,
        "source": "claude-code",
    }
    assert captured["response"] == {
        "stdout": '{"hookSpecificOutput":{}}',
        "stderr": "",
        "exit_code": 0,
        "status": "ok",
    }
    assert captured["status"] == "success"


@pytest.mark.parametrize(
    ("result", "error_message", "error_type"),
    [
        (
            {"stdout": "", "stderr": "command failed", "exit_code": 1, "status": "ok"},
            "command failed",
            "ok",
        ),
        (
            {"stdout": "", "stderr": "worker is unavailable", "exit_code": 0, "status": "worker_error"},
            "worker is unavailable",
            "worker_error",
        ),
    ],
)
def test_audit_claude_code_hook_call_records_hook_errors(
    result: dict[str, object], error_message: str, error_type: str
) -> None:
    captured = _audit(governance=FakeGovernance(), result=result)

    assert captured["status"] == "error"
    assert captured["error_message"] == error_message
    assert captured["error_type"] == error_type


def test_audit_claude_code_hook_call_records_dispatch_exception() -> None:
    captured = _audit(
        governance=FakeGovernance(),
        result={},
        exception=RuntimeError("worker crashed"),
    )

    assert captured["response"] == {"exception_type": "RuntimeError", "message": "worker crashed"}
    assert captured["status"] == "error"
    assert captured["error_message"] == "RuntimeError: worker crashed"
    assert captured["error_type"] == "hook_exception"


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


@pytest.mark.asyncio
async def test_full_probe_hook_uses_standard_audit_envelope_and_preserves_raw_payload(wm_paths) -> None:
    service = _service(wm_paths)
    raw_payload = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": "session-1",
        "cwd": "/repo",
        "prompt": "订单同步失败",
    }

    result = await service.retrieval_probe.handle_claude_code_hook(
        actor="root",
        profile_key="dev",
        event_name="UserPromptSubmit",
        matcher=None,
        payload=raw_payload,
        timeout_seconds=12,
    )

    logs = service.governance.list_logs(actor="root", source_type=SourceType.hook.value)
    assert len(logs) == 1
    log = logs[0]
    assert log["tool_name"] == "full-probe"
    assert log["status"] == "success"
    assert json.loads(log["request_json"]) == {
        "action": "full-probe",
        "event_name": "UserPromptSubmit",
        "matcher": None,
        "payload": raw_payload,
        "timeout_seconds": 12,
        "source": "claude-code",
    }
    assert json.loads(log["response_json"]) == result
    assert result == {
        "stdout": NOOP_HOOK_STDOUT,
        "stderr": "",
        "exit_code": 0,
        "status": "ok",
    }
