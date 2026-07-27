from __future__ import annotations

import logging
from typing import Any

from agent_bridge.capability_hub.models import CallLogStatus, SourceType


logger = logging.getLogger(__name__)


def audit_claude_code_hook_call(
    governance: Any | None,
    *,
    actor: str,
    profile_key: str,
    entrypoint: str,
    action: str,
    event_name: str | None,
    matcher: str | None,
    payload: dict[str, Any],
    timeout_seconds: int,
    result: dict[str, Any],
    duration_ms: int,
    exception: Exception | None = None,
) -> None:
    """记录 Claude Code Hook 的标准调用审计，不影响原 Hook 的执行结果。"""
    if governance is None:
        return

    request = {
        "action": action,
        "event_name": event_name,
        "matcher": matcher,
        "payload": payload,
        "timeout_seconds": timeout_seconds,
        "source": "claude-code",
    }
    if exception is not None:
        response = {"exception_type": type(exception).__name__, "message": str(exception)}
        status = CallLogStatus.error.value
        error_message = f"{type(exception).__name__}: {exception}"
        error_type = "hook_exception"
    else:
        response = result
        hook_status = str(result.get("status") or "")
        exit_code = int(result.get("exit_code") or 0)
        if exit_code == 0 and hook_status in {"ok", "not_configured"}:
            status = CallLogStatus.success.value
            error_message = None
            error_type = None
        else:
            status = CallLogStatus.error.value
            stderr = str(result.get("stderr") or "").strip()
            error_message = stderr or hook_status or "hook_error"
            error_type = hook_status.strip() or "hook_error"

    try:
        governance.log_tool_call(
            actor=actor,
            profile_key=profile_key,
            entrypoint=entrypoint,
            source_type=SourceType.hook.value,
            source_key="claude_code",
            tool_name=action,
            request=request,
            response=response,
            status=status,
            error_message=error_message,
            duration_ms=duration_ms,
            error_type=error_type,
        )
    except Exception:
        logger.warning("Claude Code Hook 审计写入失败 action=%s", action, exc_info=True)
