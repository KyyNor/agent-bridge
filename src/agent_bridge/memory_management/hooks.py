from __future__ import annotations

import json
import logging
from typing import Any

from agent_bridge.memory_management.models import NOOP_HOOK_STDOUT


logger = logging.getLogger(__name__)


SESSION_START_ACTION = "session-start"
NO_MEMORY_CONTEXT = "No active Agent Bridge memory block is bound to this profile."

CLAUDE_MEM_HOOK_ACTIONS = {
    "version-check",
    "start",
    "context",
    SESSION_START_ACTION,
    "session-init",
    "observation",
    "file-context",
    "summarize",
}


class MemoryHookService:
    def __init__(
        self,
        *,
        memory_service,
        worker_service: Any | None = None,
        governance_service: Any | None = None,
    ) -> None:
        self.memory_service = memory_service
        self.worker_service = worker_service
        self.governance_service = governance_service

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
        """Claude Code hook 总入口：按 action 派发到 session-start 同步或 worker.handle_hook。"""
        logger.info("memory hook 收到请求 actor=%s profile=%s action=%s", actor, profile_key, action)
        if action not in CLAUDE_MEM_HOOK_ACTIONS:
            logger.warning("memory hook 未支持的 action=%s", action)
            return {"stdout": NOOP_HOOK_STDOUT, "stderr": "", "exit_code": 0, "status": "unsupported_action"}
        if action == SESSION_START_ACTION:
            return self._handle_session_start(
                actor=actor,
                profile_key=profile_key,
                event_name=event_name,
                matcher=matcher,
                payload=payload,
                timeout_seconds=timeout_seconds,
            )
        resolved = self.memory_service.resolve_profile_block(actor, profile_key)
        if resolved["status"] != "ok":
            logger.info("memory hook 未绑定记忆块 actor=%s profile=%s status=%s", actor, profile_key, resolved["status"])
            return {"stdout": NOOP_HOOK_STDOUT, "stderr": "", "exit_code": 0, "status": resolved["status"]}
        worker = self.worker_service or self.memory_service.worker_service
        if worker is None:
            logger.warning("memory worker 服务未配置 actor=%s action=%s", actor, action)
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

    def _handle_session_start(
        self,
        *,
        actor: str,
        profile_key: str,
        event_name: str | None,
        matcher: str | None,
        payload: dict[str, Any],
        timeout_seconds: int,
    ) -> dict[str, Any]:
        """SessionStart 同步：拼装 profile 指导 + claude-mem 记忆上下文，作为 additionalContext 注入会话。"""
        resolved = self.memory_service.resolve_profile_block(actor, profile_key)
        block = resolved.get("block") if resolved["status"] == "ok" else None
        block_key = block.get("block_key") if isinstance(block, dict) else None
        logger.info("claude-mem 开始同步 block=%s actor=%s profile=%s", block_key, actor, profile_key)
        sections = [section for section in [self._profile_context(actor, profile_key)] if section]
        memory_status = resolved["status"]
        if memory_status == "ok":
            worker = self.worker_service or self.memory_service.worker_service
            if worker is None:
                logger.warning("claude-mem 同步跳过：worker 服务未配置 actor=%s", actor)
                sections.append("Agent Bridge memory context is unavailable: memory worker service is not configured.")
                memory_status = "worker_error"
            else:
                result = worker.handle_hook(
                    resolved["block"],
                    action="context",
                    payload=payload,
                    event_name=event_name,
                    matcher=matcher,
                    timeout_seconds=timeout_seconds,
                )
                memory_status = str(result.get("status") or "ok")
                memory_context = self._extract_additional_context(str(result.get("stdout") or ""))
                if memory_context:
                    sections.append(memory_context)
                elif memory_status != "ok":
                    sections.append(f"Agent Bridge memory context is unavailable: {memory_status}.")
        else:
            sections.append(NO_MEMORY_CONTEXT)

        context = "\n\n".join(section.strip() for section in sections if section.strip())
        if not context:
            context = "Agent Bridge session context is unavailable for this profile."
        final_status = "ok" if memory_status in {"ok", "not_configured"} else memory_status
        if final_status == "ok":
            logger.info("claude-mem 同步完成 block=%s", block_key)
        else:
            logger.warning("claude-mem 同步未就绪 block=%s status=%s", block_key, final_status)
        return {
            "stdout": self._session_start_stdout(context),
            "stderr": "",
            "exit_code": 0,
            "status": final_status,
        }

    def _profile_context(self, actor: str, profile_key: str) -> str:
        if self.governance_service is None:
            return ""
        try:
            rendered = self.governance_service.render_profile_markdown(actor, profile_key)
        except Exception as exc:
            return f"Agent Bridge profile guidance is unavailable: {exc}"
        markdown = rendered.get("markdown")
        return str(markdown) if isinstance(markdown, str) else ""

    def _session_start_stdout(self, context: str) -> str:
        return json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": context,
                }
            },
            ensure_ascii=False,
        )

    def _extract_additional_context(self, stdout: str) -> str:
        stripped = stdout.strip()
        if not stripped or stripped == NOOP_HOOK_STDOUT:
            return ""
        try:
            loaded = json.loads(stripped)
        except json.JSONDecodeError:
            return stripped
        if not isinstance(loaded, dict):
            return stripped
        hook_output = loaded.get("hookSpecificOutput")
        if isinstance(hook_output, dict):
            additional_context = hook_output.get("additionalContext")
            if isinstance(additional_context, str):
                return additional_context.strip()
        if loaded.get("suppressOutput"):
            return ""
        return stripped
