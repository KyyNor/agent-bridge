from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from typing import Any

from fastapi import Request

from agent_bridge.automation.workflows.runtime_capability import WORKFLOW_CAPABILITY_HEADER
from agent_bridge.core.domain import AccessDenied


def profile_from_headers(request: Request) -> str | None:
    value = request.headers.get("x-agent-bridge-metamcp-profile", "").strip()
    return value or None


def workflow_context_from_headers(request: Request) -> dict[str, Any] | None:
    workflow_enabled = request.headers.get("x-agent-bridge-workflow", "").strip().lower() == "true"
    workflow_key = request.headers.get("x-agent-bridge-workflow-key", "").strip()
    run_id = request.headers.get("x-agent-bridge-workflow-run-id", "").strip()
    if not workflow_enabled:
        return None
    return {
        "workflow": True,
        "workflow_key": workflow_key or None,
        "run_id": run_id or None,
    }


def resolve_workflow_runtime_identity(
    workflow_service: Any,
    *,
    capability_token: str,
    workflow_context: dict[str, Any] | None,
    profile_key: str | None,
    fallback_actor: str,
) -> tuple[str, str | None, AbstractContextManager[None]]:
    """解析请求中工作流运行 capability 对应的执行主体与归属组作用域。

    携带 capability header 时必须同时携带完整运行上下文，校验通过后返回服务端
    签发的运行时主体并绑定其归属组作用域（不给管理员旁路）；未携带时回退到
    请求自身的用户主体。
    """
    if not capability_token:
        return fallback_actor, profile_key, nullcontext()
    if not workflow_context or not workflow_context.get("workflow_key") or not workflow_context.get("run_id"):
        raise AccessDenied("工作流 capability 缺少运行上下文")
    capability = workflow_service.require_runtime_capability(
        capability_token,
        workflow_key=str(workflow_context["workflow_key"]),
        run_id=str(workflow_context["run_id"]),
        profile_key=profile_key,
    )
    return (
        capability.actor,
        capability.profile_key,
        workflow_service.bind_runtime_capability(capability),
    )
