from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from agent_bridge.agent_runtime.adapters.jsonl_cli import effective_prompt as _effective_prompt
from agent_bridge.agent_runtime.adapters.opencode_events import (
    _OpenCodeEventMapper,
    _events_from_opencode_response,
    _opencode_event,
)
from agent_bridge.agent_runtime.adapters.opencode_server import (
    OpenCodeServerError,
    OpenCodeServerProcess,
)
from agent_bridge.agent_runtime.types import (
    CodingAgentCapabilities,
    CodingAgentFinal,
    CodingAgentRequest,
    CodingAgentRun,
    CodingAgentUpdate,
)


@dataclass
class _OpenCodeRun:
    request: CodingAgentRequest
    command: str
    model: str | None = None
    auto_approve: bool = True
    _server: OpenCodeServerProcess | None = field(default=None, init=False)

    async def updates(self) -> AsyncIterator[CodingAgentUpdate]:
        directory = str(self.request.cwd.resolve())
        server = OpenCodeServerProcess(command=self.command)
        self._server = server
        stream_task: asyncio.Task[None] | None = None
        try:
            await server.start(cwd=self.request.cwd)
            session_response = await server.request_json(
                "POST",
                "/session",
                params={"directory": directory},
                payload={},
            )
            session_id = _session_id(session_response)
            if not session_id:
                raise OpenCodeServerError("OpenCode server 创建会话未返回 session id")

            queue: asyncio.Queue[Any] = asyncio.Queue()
            stream_task = asyncio.create_task(
                _pump_opencode_events(
                    server,
                    directory=directory,
                    queue=queue,
                )
            )
            # Let the SSE request get established before prompt_async. This
            # prevents losing the first step/message event on fast responses.
            await asyncio.sleep(0)
            await server.request_no_content(
                "POST",
                f"/session/{session_id}/prompt_async",
                params={"directory": directory},
                payload=_message_payload(self.request, self.model),
            )

            mapper = _OpenCodeEventMapper(session_id=session_id)
            while True:
                item = await queue.get()
                if item is None:
                    raise OpenCodeServerError("OpenCode server SSE 在会话结束前断开")
                if isinstance(item, _OpenCodeStreamFailure):
                    raise item.error
                if self.request.on_native_message is not None:
                    self.request.on_native_message(item)
                events, final = mapper.consume(item)
                if events or final is not None:
                    yield CodingAgentUpdate(raw=item, events=events, final=final)
                if mapper.done:
                    return
        except asyncio.CancelledError:
            raise
        except OpenCodeServerError as exc:
            yield CodingAgentUpdate(
                events=[_opencode_event("error", status="failed", message=str(exc))],
                final=CodingAgentFinal(is_error=True, result=str(exc)),
            )
        finally:
            if stream_task is not None and not stream_task.done():
                stream_task.cancel()
                try:
                    await stream_task
                except asyncio.CancelledError:
                    pass
            await server.close()
            self._server = None

    async def abort(self) -> None:
        if self._server is not None:
            await self._server.abort()


@dataclass(frozen=True)
class _OpenCodeStreamFailure:
    error: OpenCodeServerError


async def _pump_opencode_events(
    server: OpenCodeServerProcess,
    *,
    directory: str,
    queue: asyncio.Queue[Any],
) -> None:
    """Bridge the long-lived SSE iterator to the adapter's update loop."""
    try:
        async for payload in server.stream_json_events(
            "/event",
            params={"directory": directory},
        ):
            await queue.put(payload)
    except asyncio.CancelledError:
        raise
    except OpenCodeServerError as exc:
        await queue.put(_OpenCodeStreamFailure(exc))
    finally:
        await queue.put(None)


def _message_payload(request: CodingAgentRequest, configured_model: str | None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "parts": [{"type": "text", "text": _effective_prompt(request)}],
    }
    model = _model_payload(request.model or configured_model)
    if model is not None:
        payload["model"] = model
    if request.output_schema:
        payload["format"] = {
            "type": "json_schema",
            "schema": request.output_schema,
            "retryCount": 2,
        }
    return payload


def _model_payload(value: str | None) -> dict[str, str] | None:
    if not value:
        return None
    provider_id, separator, model_id = value.partition("/")
    if not separator or not provider_id or not model_id:
        return None
    return {"providerID": provider_id, "modelID": model_id}


def _session_id(response: Any) -> str | None:
    if not isinstance(response, dict):
        return None
    value = response.get("id") or response.get("sessionID")
    return value if isinstance(value, str) and value else None


class OpenCodeCodingAgent:
    source = "opencode_server"
    capabilities = CodingAgentCapabilities(
        supports_mcp=True,
        supports_native_json_schema=True,
        supports_skills=False,
        supports_subagents=False,
        supports_cost=True,
        supports_turn_count=True,
        supports_abort=True,
        supports_partial_messages=True,
    )

    def __init__(
        self,
        *,
        backend_key: str = "opencode",
        command: str = "opencode",
        model: str | None = None,
        auto_approve: bool = True,
    ) -> None:
        self.backend_key = backend_key
        self.display_name = "OpenCode"
        self.command = command
        self.model = model
        self.auto_approve = auto_approve

    def start(self, request: CodingAgentRequest) -> CodingAgentRun:
        return _OpenCodeRun(
            request=request,
            command=self.command,
            model=self.model,
            auto_approve=self.auto_approve,
        )
