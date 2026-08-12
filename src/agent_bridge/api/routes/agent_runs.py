"""Agent run log endpoints: list and inspect AgentService invocations."""
from __future__ import annotations

import json
import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import StreamingResponse

from agent_bridge.api.schemas import DesignAgentRequest
from agent_bridge.agent_runtime.json_schema import DRAFT7_SCHEMA_URI
from agent_bridge.agent_runtime.trace import read_payload
from agent_bridge.automation.workflows.definition import WorkflowGraph
from agent_bridge.access_control.service import ResourceScope
from agent_bridge.core.domain import ConflictError, NotFound

logger = logging.getLogger(__name__)


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


def _live_or_persisted_events(row: dict[str, Any]) -> list[dict[str, Any]]:
    """读取运行中的 JSONL；终态时优先包含完整终态事件的 SQLite 副本。"""
    cwd = row.get("cwd")
    if cwd:
        events = _read_events_jsonl(Path(str(cwd)) / "events.jsonl")
        if events is not None:
            persisted = row.get("events") or []
            if row.get("status") != "running" and len(persisted) > len(events):
                return persisted
            return events
    return row.get("events") or []


def _event_id(record: dict[str, Any], fallback: int) -> int:
    value = record.get("event_id")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return fallback


def _events_with_ids(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """为旧运行的无 id 事件按稳定顺序投影事件号。"""
    normalized: list[dict[str, Any]] = []
    for index, event in enumerate(events, start=1):
        item = dict(event)
        item["event_id"] = _event_id(item, index)
        normalized.append(item)
    return normalized


def _sse_frame(event: str, payload: dict[str, Any], event_id: int | None = None) -> str:
    lines: list[str] = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append("data: " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return "\n".join(lines) + "\n\n"


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

BUSINESS_LEDGER_DESIGN_SCHEMA: dict[str, Any] = {
    "$schema": DRAFT7_SCHEMA_URI,
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "notes", "ledger"],
    "properties": {
        "summary": {"type": "string"},
        "notes": {"type": "array", "items": {"type": "string"}},
        "ledger": {
            "type": "object",
            "additionalProperties": False,
            "required": ["ledger_key", "name", "description", "fields"],
            "properties": {
                "ledger_key": {"type": "string", "pattern": "^[a-z0-9_-]{1,80}$"},
                "name": {"type": "string", "minLength": 1},
                "description": {"type": "string"},
                "fields": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 100,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["field_key", "name", "field_type", "required", "fuzzy_match", "agent_readable", "enum_values"],
                        "properties": {
                            "field_key": {"type": "string", "pattern": "^[a-z0-9_-]{1,80}$"},
                            "name": {"type": "string", "minLength": 1},
                            "field_type": {"type": "string", "enum": ["text", "number", "enum", "date", "datetime"]},
                            "required": {"type": "boolean"},
                            "fuzzy_match": {"type": "boolean"},
                            "agent_readable": {"type": "boolean"},
                            "enum_values": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
                        },
                    },
                },
            },
        },
    },
}

def create_agent_runs_routes(service, actor):
    router = APIRouter()

    def require_run_read(current_actor: str, run_key: str) -> dict[str, Any]:
        row = service.store.agent_runs.get(run_key)
        if row is None:
            raise NotFound("agent run not found")
        service.access.require_read(
            actor=current_actor,
            scope=ResourceScope.from_record(row),
        )
        return row

    def require_run_write(current_actor: str, run_key: str) -> dict[str, Any]:
        row = require_run_read(current_actor, run_key)
        service.access.require_write(
            actor=current_actor,
            scope=ResourceScope.from_record(row),
        )
        return row

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
        enforce_scope = current_actor not in service.admins
        viewer_group_key = (
            service.access.actor_group_key(current_actor, required=True)
            if enforce_scope
            else None
        )
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
                viewer_group_key=viewer_group_key,
                enforce_scope=enforce_scope,
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
            viewer_group_key=viewer_group_key,
            enforce_scope=enforce_scope,
        )

    @router.get("/agent-runs/{run_key}")
    def get_agent_run(run_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        row = require_run_read(current_actor, run_key)
        return _normalize_agent_run_result(row)

    @router.get("/agent-runs/{run_key}/events")
    def get_agent_run_events(run_key: str, current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        """Live event stream for an agent run.

        Reads ``events.jsonl`` from the run's work directory (written in real
        time as the agent streams), so progress polling sees events before the
        run finishes. Falls back to the persisted ``events_json`` (flushed at
        completion) for historical runs whose work directory is gone."""
        row = require_run_read(current_actor, run_key)
        return _live_or_persisted_events(row)

    @router.get("/agent-runs/{run_key}/events/stream")
    async def stream_agent_run_events(
        run_key: str,
        request: Request,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
        current_actor: str = Depends(actor),
    ) -> StreamingResponse:
        """以 SSE 推送已写入 JSONL 的 Agent 事件，并支持断线重放。"""
        row = require_run_read(current_actor, run_key)
        try:
            cursor = max(0, int(last_event_id or "0"))
        except ValueError:
            cursor = 0
        subscription = service.agents.live_events.subscribe(run_key)
        logger.info("Agent run SSE 订阅建立 run_key=%s last_event_id=%d", run_key, cursor)

        async def generate():
            latest_id = cursor
            try:
                # 先订阅再读取持久快照，避免快照与订阅之间的事件丢失；队列中的重复事件
                # 会通过 event_id 被忽略。
                current = service.store.agent_runs.get(run_key)
                if current is None:
                    yield _sse_frame("run_terminal", {"run_key": run_key, "status": "missing", "ok": False})
                    return
                for record in _events_with_ids(_live_or_persisted_events(current)):
                    record_id = int(record["event_id"])
                    if record_id <= latest_id:
                        continue
                    latest_id = record_id
                    yield _sse_frame("agent_event", record, record_id)
                if current.get("status") != "running":
                    yield _sse_frame(
                        "run_terminal",
                        {
                            "run_key": run_key,
                            "status": current.get("status"),
                            "ok": current.get("ok", False),
                            "error": current.get("error"),
                        },
                    )
                    return
                while not await request.is_disconnected():
                    try:
                        message = await asyncio.wait_for(subscription.receive(), timeout=20)
                    except TimeoutError:
                        yield _sse_frame("heartbeat", {})
                        continue
                    if message.kind == "agent_event":
                        record = dict(message.payload)
                        record_id = _event_id(record, latest_id + 1)
                        if record_id <= latest_id:
                            continue
                        record["event_id"] = record_id
                        latest_id = record_id
                        yield _sse_frame("agent_event", record, record_id)
                    elif message.kind == "resync_required":
                        yield _sse_frame("resync_required", message.payload)
                        return
                    elif message.kind == "run_terminal":
                        yield _sse_frame("run_terminal", message.payload)
                        return
            finally:
                service.agents.live_events.unsubscribe(subscription)
                logger.info("Agent run SSE 订阅关闭 run_key=%s last_event_id=%d", run_key, latest_id)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @router.get("/agent-runs/{run_key}/payload")
    def get_agent_run_payload(
        run_key: str,
        ref: str,
        current_actor: str = Depends(actor),
    ) -> Response:
        """Read a large tool input/output stored below the run directory."""
        row = require_run_read(current_actor, run_key)
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
        row = service.store.agent_runs.get(run_key)
        if row is not None:
            row = require_run_write(current_actor, run_key)
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

        row = require_run_read(current_actor, run_key)
        cwd = row.get("cwd")
        if not cwd:
            return {"task_id": task_id, "transcript_dir": None, "agents": []}
        return build_subagent_detail(Path(str(cwd)), task_id)

    @router.post("/agent-runs/design/workflow")
    async def design_workflow(
        payload: DesignAgentRequest,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
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

    @router.post("/agent-runs/design/business-ledger")
    async def design_business_ledger(
        payload: DesignAgentRequest,
        current_actor: str = Depends(actor),
    ) -> dict[str, Any]:
        result = await service.agents.run(
            prompt=_design_prompt(
                kind="business ledger",
                skill_name="design_business_ledger",
                skill_prompt=service.skills.get_skill(current_actor, "design_business_ledger")["prompt"],
                payload=payload,
                expected_artifact="business ledger definition",
            ),
            agent_name="design_business_ledger",
            profile=payload.profile_key or _str_or_none(payload.current.get("profile_key")),
            output_schema=BUSINESS_LEDGER_DESIGN_SCHEMA,
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
