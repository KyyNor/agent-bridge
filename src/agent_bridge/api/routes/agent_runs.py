"""Agent run log endpoints: list and inspect AgentService invocations."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends

from agent_bridge.api.schemas import DesignAgentRequest
from agent_bridge.core.domain import NotFound, require_admin_user


WORKFLOW_DESIGN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "workflow"],
    "properties": {
        "summary": {"type": "string"},
        "notes": {"type": "array", "items": {"type": "string"}},
        "workflow": {
            "type": "object",
            "additionalProperties": False,
            "required": ["workflow_key", "name", "description", "profile_key", "status", "workflow_js"],
            "properties": {
                "workflow_key": {"type": "string"},
                "name": {"type": "string"},
                "description": {"type": "string"},
                "profile_key": {"type": "string"},
                "status": {"type": "string", "enum": ["active", "disabled"]},
                "workflow_js": {"type": "string"},
            },
        },
    },
}

SCRIPT_DESIGN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "script"],
    "properties": {
        "summary": {"type": "string"},
        "notes": {"type": "array", "items": {"type": "string"}},
        "script": {
            "type": "object",
            "additionalProperties": False,
            "required": ["script_key", "name", "description", "language", "code", "status", "owner_type", "owner_key"],
            "properties": {
                "script_key": {"type": "string"},
                "name": {"type": "string"},
                "description": {"type": "string"},
                "language": {"type": "string", "enum": ["python"]},
                "code": {"type": "string"},
                "status": {"type": "string", "enum": ["active", "disabled"]},
                "owner_type": {"type": "string"},
                "owner_key": {"type": "string"},
            },
        },
    },
}

def create_agent_runs_routes(service, actor):
    router = APIRouter()

    @router.get("/agent-runs")
    def list_agent_runs(
        agent_name: str | None = None,
        profile_key: str | None = None,
        workflow_key: str | None = None,
        workflow_run_id: str | None = None,
        ok: bool | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
        limit: int = 50,
        offset: int = 0,
        current_actor: str = Depends(actor),
    ) -> list[dict[str, Any]]:
        return service.store.agent_runs.list(
            agent_name=agent_name,
            profile_key=profile_key,
            workflow_key=workflow_key,
            workflow_run_id=workflow_run_id,
            ok=ok,
            created_from=created_from,
            created_to=created_to,
            limit=limit,
            offset=offset,
        )

    @router.get("/agent-runs/{run_key}")
    def get_agent_run(run_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        row = service.store.agent_runs.get(run_key)
        if row is None:
            raise NotFound("agent run not found")
        return row

    @router.get("/agent-runs/{run_key}/subagent-detail")
    def get_agent_run_subagent_detail(
        run_key: str,
        task_id: str,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        """Sub-agent (Task) transcript detail for any agent run.

        Works for every ``AgentService`` run — workflow, understand, design —
        because raw SDK messages are persisted uniformly to ``messages.jsonl``
        in the run's work directory."""
        from pathlib import Path

        from agent_bridge.agent_runtime.subagent_details import build_subagent_detail

        row = service.store.agent_runs.get(run_key)
        if row is None:
            raise NotFound("agent run not found")
        cwd = row.get("cwd")
        if not cwd:
            return {"task_id": task_id, "transcript_dir": None, "agents": []}
        return build_subagent_detail(Path(str(cwd)), task_id)

    @router.post("/agent-runs/design/workflow")
    async def design_workflow(
        payload: DesignAgentRequest,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        require_admin_user(current_actor, service.admins)
        result = await service.agents.run(
            prompt=_design_prompt(
                kind="workflow",
                skill_name="design_workflow",
                skill_prompt=service.skills.get_skill(current_actor, "design_workflow")["prompt"],
                payload=payload,
                expected_file="workflow.js",
            ),
            agent_name="design_workflow",
            profile=payload.profile_key or _str_or_none(payload.current.get("profile_key")),
            output_schema=WORKFLOW_DESIGN_SCHEMA,
            actor=current_actor,
            timeout=900,
        )
        return _design_response(service, result)

    @router.post("/agent-runs/design/script")
    async def design_script(
        payload: DesignAgentRequest,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        require_admin_user(current_actor, service.admins)
        result = await service.agents.run(
            prompt=_design_prompt(
                kind="script",
                skill_name="design_script",
                skill_prompt=service.skills.get_skill(current_actor, "design_script")["prompt"],
                payload=payload,
                expected_file="script.py",
            ),
            agent_name="design_script",
            profile=payload.profile_key or _str_or_none(payload.current.get("profile_key")),
            output_schema=SCRIPT_DESIGN_SCHEMA,
            actor=current_actor,
            timeout=900,
        )
        return _design_response(service, result)

    return router


def _design_prompt(
    *,
    kind: str,
    skill_name: str,
    skill_prompt: str,
    payload: DesignAgentRequest,
    expected_file: str,
) -> str:
    mode = "modify" if payload.mode == "modify" else "create"
    return "\n\n".join(
        [
            f"你是 Agent Bridge 的 {kind} 设计 agent。请根据用户需求生成可直接采纳的 {expected_file}。",
            f"必须先遵循内置技能 {skill_name}。如果你需要工具，请优先执行 execute service='built-in' tool_name='load_skill' params={{\"skill_name\":\"{skill_name}\"}}；下方也内联提供了当前技能内容作为约束。",
            "采纳结果会直接写回系统，所以请返回完整字段，不要只给 patch 或解释。",
            f"模式：{mode}。modify 表示在当前对象基础上改；create 表示生成一个新对象。",
            "用户提示词：\n" + payload.prompt.strip(),
            "当前对象 JSON：\n" + json.dumps(payload.current, ensure_ascii=False, indent=2),
            f"{skill_name} 内容：\n{skill_prompt}",
        ]
    )


def _design_response(service: Any, result: Any) -> dict[str, Any]:
    detail = service.store.agent_runs.get(result.run_key) if result.run_key else None
    return {
        "ok": result.ok,
        "error": result.error,
        "run_key": result.run_key,
        "result": result.result,
        "agent_run": detail,
    }


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
