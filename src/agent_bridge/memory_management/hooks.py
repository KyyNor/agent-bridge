from __future__ import annotations

from typing import Any

from agent_bridge.memory_management.models import NOOP_HOOK_STDOUT


CLAUDE_MEM_HOOK_ACTIONS = {
    "version-check",
    "start",
    "context",
    "session-init",
    "observation",
    "file-context",
    "summarize",
}


class MemoryHookService:
    def __init__(self, *, memory_service, worker_service: Any | None = None) -> None:
        self.memory_service = memory_service
        self.worker_service = worker_service

    def handle_claude_code_hook(
        self,
        *,
        actor: str,
        profile_key: str,
        action: str,
        event_name: str | None,
        matcher: str | None,
        payload: dict[str, Any],
        timeout_seconds: int,
    ) -> dict[str, Any]:
        if action not in CLAUDE_MEM_HOOK_ACTIONS:
            return {"stdout": NOOP_HOOK_STDOUT, "stderr": "", "exit_code": 0, "status": "unsupported_action"}
        resolved = self.memory_service.resolve_profile_block(actor, profile_key)
        if resolved["status"] != "ok":
            return {"stdout": NOOP_HOOK_STDOUT, "stderr": "", "exit_code": 0, "status": resolved["status"]}
        worker = self.worker_service or self.memory_service.worker_service
        if worker is None:
            return {
                "stdout": NOOP_HOOK_STDOUT,
                "stderr": "memory worker service is not configured",
                "exit_code": 0,
                "status": "worker_error",
            }
        return worker.handle_hook(
            resolved["block"],
            action=action,
            payload=payload,
            event_name=event_name,
            matcher=matcher,
            timeout_seconds=timeout_seconds,
        )
