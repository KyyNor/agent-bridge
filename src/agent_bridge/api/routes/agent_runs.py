"""Agent run log endpoints: list and inspect AgentService invocations."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Response

from agent_bridge.api.schemas import DesignAgentRequest
from agent_bridge.agent_runtime.json_schema import DRAFT7_SCHEMA_URI
from agent_bridge.agent_runtime.trace import read_payload
from agent_bridge.automation.workflows.definition import WorkflowGraph
from agent_bridge.core.domain import ConflictError, NotFound, require_admin_user


def _read_events_jsonl(path: Path) -> list[dict[str, Any]] | None:
    """Read a run's live ``events.jsonl``. Returns None if the file is absent
    (so callers can fall back to the persisted DB events)."""
    if not path.is_file():
        return None
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            events.append(item)
    return events


def _normalize_agent_run_result(row: dict[str, Any]) -> dict[str, Any]:
    """Recover structured output for older non-native-schema agent runs.

    Some adapters stream the useful JSON as an assistant text event while their
    terminal result row only says ``done``. Keep the stored row immutable, but
    make detail reads useful by deriving the schema result from the event log.
    """
    if not row.get("output_schema"):
        return row
    if not _is_generic_result(row.get("result")):
        return row
    recovered = _extract_json_from_agent_events(row.get("events") or [])
    if recovered is None:
        return row
    normalized = dict(row)
    normalized["result"] = recovered
    return normalized


def _is_generic_result(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() in {
        "",
        "done",
        "success",
        "succeeded",
        "complete",
        "completed",
        "ok",
    }


def _extract_json_from_agent_events(events: list[dict[str, Any]]) -> Any | None:
    for event in reversed(events):
        if event.get("kind") != "agent_message":
            continue
        message = event.get("message")
        if not isinstance(message, str):
            continue
        parsed = _extract_json(message)
        if parsed is not None:
            return parsed
    return None


def _extract_json(text: str) -> Any | None:
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


_WORKFLOW_GRAPH_SCHEMA = WorkflowGraph.model_json_schema(ref_template="#/definitions/{model}")
_WORKFLOW_GRAPH_DEFS = _WORKFLOW_GRAPH_SCHEMA.pop("$defs", {})
_WORKFLOW_GRAPH_SCHEMA["required"] = ["nodes", "edges"]

WORKFLOW_DESIGN_SCHEMA: dict[str, Any] = {
    "$schema": DRAFT7_SCHEMA_URI,
    "definitions": _WORKFLOW_GRAPH_DEFS,
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "notes", "workflow"],
    "properties": {
        "summary": {"type": "string"},
        "notes": {"type": "array", "items": {"type": "string"}},
        "workflow": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "workflow_key",
                "name",
                "description",
                "profile_key",
                "workflow_type",
                "status",
                "definition",
            ],
            "properties": {
                "workflow_key": {"type": "string"},
                "name": {"type": "string"},
                "description": {"type": "string"},
                "profile_key": {"type": "string"},
                "workflow_type": {"type": "string", "enum": ["operation", "summary"]},
                "status": {"type": "string", "enum": ["active", "disabled"]},
                "definition": _WORKFLOW_GRAPH_SCHEMA,
            },
        },
    },
}

SCRIPT_DESIGN_SCHEMA: dict[str, Any] = {
    "$schema": DRAFT7_SCHEMA_URI,
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "script"],
    "properties": {
        "summary": {"type": "string"},
        "notes": {"type": "array", "items": {"type": "string"}},
        "script": {
            "type": "object",
            "additionalProperties": False,
            "required": ["script_key", "name", "description", "language", "code", "input_schema", "output_schema", "status", "owner_type", "owner_key"],
            "properties": {
                "script_key": {"type": "string"},
                "name": {"type": "string"},
                "description": {"type": "string"},
                "language": {"type": "string", "enum": ["python"]},
                "code": {"type": "string"},
                "input_schema": {
                    "type": "object",
                    "required": ["type", "properties"],
                    "properties": {
                        "type": {"const": "object"},
                        "properties": {
                            "type": "object",
                            "additionalProperties": {"type": "object"},
                        },
                        "required": {
                            "type": "array",
                            "items": {"type": "string"},
                            "uniqueItems": True,
                        },
                    },
                },
                "output_schema": {
                    "anyOf": [
                        {"type": "object"},
                        {"type": "null"},
                    ],
                },
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
        status: str | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
        paginated: bool = False,
        current_actor: str = Depends(actor),
    ) -> list[dict[str, Any]] | dict[str, Any]:
        if paginated:
            return service.store.agent_runs.list_paginated(
                agent_name=agent_name,
                profile_key=profile_key,
                workflow_key=workflow_key,
                workflow_run_id=workflow_run_id,
                ok=ok,
                status=status,
                created_from=created_from,
                created_to=created_to,
                search=search,
                limit=limit,
                offset=offset,
            )
        return service.store.agent_runs.list(
            agent_name=agent_name,
            profile_key=profile_key,
            workflow_key=workflow_key,
            workflow_run_id=workflow_run_id,
            ok=ok,
            status=status,
            created_from=created_from,
            created_to=created_to,
            limit=limit,
            offset=offset,
            search=search,
        )

    @router.get("/agent-runs/{run_key}")
    def get_agent_run(run_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        row = service.store.agent_runs.get(run_key)
        if row is None:
            raise NotFound("agent run not found")
        return _normalize_agent_run_result(row)

    @router.get("/agent-runs/{run_key}/events")
    def get_agent_run_events(run_key: str, current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        """Live event stream for an agent run.

        Reads ``events.jsonl`` from the run's work directory (written in real
        time as the agent streams), so progress polling sees events before the
        run finishes. Falls back to the persisted ``events_json`` (flushed at
        completion) for historical runs whose work directory is gone."""
        row = service.store.agent_runs.get(run_key)
        if row is None:
            raise NotFound("agent run not found")
        cwd = row.get("cwd")
        if cwd:
            events = _read_events_jsonl(Path(str(cwd)) / "events.jsonl")
            if events is not None:
                persisted = row.get("events") or []
                if row.get("status") != "running" and len(persisted) > len(events):
                    return persisted
                return events
        return row.get("events") or []

    @router.get("/agent-runs/{run_key}/payload")
    def get_agent_run_payload(
        run_key: str,
        ref: str,
        current_actor: str = Depends(actor),
    ) -> Response:
        """Read a large tool input/output stored below the run directory."""
        row = service.store.agent_runs.get(run_key)
        if row is None:
            raise NotFound("agent run not found")
        cwd = row.get("cwd")
        if not cwd:
            raise NotFound("agent run payload not found")
        try:
            content, media_type = read_payload(Path(str(cwd)), ref)
        except (FileNotFoundError, OSError, ValueError) as exc:
            raise NotFound("agent run payload not found") from exc
        return Response(content=content, media_type=media_type)

    @router.post("/agent-runs/{run_key}/stop")
    def stop_agent_run(
        run_key: str,
        response: Response,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        require_admin_user(current_actor, service.admins)
        row = service.store.agent_runs.get(run_key)
        if row is not None and row.get("status") != "running":
            return row
        active = service.agents.has_active_control(run_key)
        pending = service.agents.has_pending_control(run_key)
        if row is None:
            if not active and not pending:
                raise NotFound("agent run not found")
        elif not active:
            raise ConflictError("agent run controller is not available")
        service.agents.request_stop(run_key)
        response.status_code = 202
        return {"status": "stopping", "run_key": run_key}

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
                expected_artifact="structured workflow definition",
            ),
            agent_name="design_workflow",
            profile=payload.profile_key or _str_or_none(payload.current.get("profile_key")),
            output_schema=WORKFLOW_DESIGN_SCHEMA,
            actor=current_actor,
            run_key=payload.run_key,
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
                expected_artifact="script.py",
            ),
            agent_name="design_script",
            profile=payload.profile_key or _str_or_none(payload.current.get("profile_key")),
            output_schema=SCRIPT_DESIGN_SCHEMA,
            actor=current_actor,
            run_key=payload.run_key,
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
    expected_artifact: str,
) -> str:
    mode = "modify" if payload.mode == "modify" else "create"
    return "\n\n".join(
        [
            f"你是 Agent Bridge 的 {kind} 设计 agent。请根据用户需求生成可直接采纳的 {expected_artifact}。",
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
