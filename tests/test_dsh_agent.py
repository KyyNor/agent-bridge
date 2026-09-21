"""DSH（ACP）Coding Agent 后端的装配、事件映射与 run 生命周期。"""

from __future__ import annotations

import asyncio
import json
import os
import pwd
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from agent_bridge.agent_runtime.adapters.dsh import (
    MODEL_PATCH_FILENAME,
    AcpProcessExitedError,
    DshCodingAgent,
    acp_mcp_servers,
    build_model_patch,
    events_from_acp_update,
    final_from_prompt_result,
    write_model_patch,
)
from agent_bridge.agent_runtime.registry import create_coding_agent_registry
from agent_bridge.agent_runtime.types import CodingAgentRequest, CodingAgentRunContext
from agent_bridge.core.config import AgentBackendConfig, AgentRuntimeConfig
from agent_bridge.dsh.agent_runtime import DshAgentRuntimeResolver


def _request(tmp_path: Path, **overrides: Any) -> CodingAgentRequest:
    payload: dict[str, Any] = {
        "prompt": "ping",
        "cwd": tmp_path,
        "mcp_servers": {},
        "setting_sources": [],
        "run_context": CodingAgentRunContext(actor="tester", owner_group_key="grp"),
    }
    payload.update(overrides)
    return CodingAgentRequest(**payload)


# -- 装配与校验 --


def test_registry_discovers_dsh_backend() -> None:
    registry = create_coding_agent_registry(
        AgentRuntimeConfig(
            default_backend="dsh",
            backends=(AgentBackendConfig(slug="dsh", agent_type="dsh", model="deepseek-flash"),),
        )
    )

    assert "dsh" in registry.keys()
    agent = registry.get("dsh")
    assert agent.display_name == "DSH"
    assert agent.source == "dsh_acp"
    assert agent.capabilities.supports_mcp is True
    assert agent.capabilities.supports_native_json_schema is False
    assert agent.capabilities.supports_abort is True


def test_registry_rejects_effort_for_dsh() -> None:
    # DSH 托管供应商路由不暴露 reasoning effort，配置 effort 必须被明确拒绝。
    with pytest.raises(ValueError, match="不支持配置思考力度"):
        create_coding_agent_registry(
            AgentRuntimeConfig(
                backends=(AgentBackendConfig(slug="dsh", agent_type="dsh", effort="high"),),
            )
        )


def test_dsh_run_without_runtime_dependency_fails_clearly(tmp_path: Path) -> None:
    run = DshCodingAgent(runtime=None).start(_request(tmp_path))

    async def consume() -> list[Any]:
        with pytest.raises(RuntimeError, match="dsh_runtime"):
            async for _ in run.updates():
                pass
        return []

    asyncio.run(consume())


# -- 模型路由 patch 与 MCP 声明 --


def test_build_model_patch_overrides_acp_route(tmp_path: Path) -> None:
    path = write_model_patch(tmp_path / MODEL_PATCH_FILENAME, provider="agent-bridge", model="deepseek-flash")

    assert path.name == MODEL_PATCH_FILENAME
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert document == [{"id": "acp", "config": {"provider": "agent-bridge", "model": "deepseek-flash"}}]
    assert build_model_patch(provider="p", model="m") == [
        {"id": "acp", "config": {"provider": "p", "model": "m"}}
    ]


def test_acp_mcp_servers_converts_http_and_stdio(tmp_path: Path) -> None:
    config = {
        "mcpServers": {
            "agent-bridge": {
                "type": "http",
                "url": "http://127.0.0.1:8765/mcp",
                "headers": {"X-Agent-Bridge-MetaMCP-Profile": "demo"},
                "timeout": 300000,
            },
            "local": {"command": "/usr/bin/mcp", "args": ["--flag"], "env": {"K": "V"}},
            "broken": {"type": "http"},
        }
    }
    path = tmp_path / ".mcp.json"
    path.write_text(json.dumps(config), encoding="utf-8")

    servers = acp_mcp_servers(path)

    assert servers == [
        {
            "type": "http",
            "name": "agent-bridge",
            "url": "http://127.0.0.1:8765/mcp",
            "headers": [{"name": "X-Agent-Bridge-MetaMCP-Profile", "value": "demo"}],
        },
        {
            "name": "local",
            "command": "/usr/bin/mcp",
            "args": ["--flag"],
            "env": [{"name": "K", "value": "V"}],
        },
    ]
    # dict 输入与文件路径输入等价。
    assert acp_mcp_servers(config) == servers
    assert acp_mcp_servers(tmp_path / "missing.json") == []
    assert acp_mcp_servers({"mcpServers": {}}) == []


# -- 事件映射 --


def test_events_from_acp_update_message_and_thought() -> None:
    message = events_from_acp_update(
        {
            "sessionUpdate": "agent_message_chunk",
            "messageId": "m-1",
            "content": {"type": "text", "text": "HELLO"},
        },
        session_id="s-1",
    )
    assert len(message) == 1
    record = message[0]
    assert record["kind"] == "agent_message"
    assert record["agent_name"] == "dsh"
    assert record["source"] == "dsh_acp"
    assert record["message"] == "HELLO"
    assert record["stream_id"] == "m-1"
    assert record["partial"] is True

    thought = events_from_acp_update(
        {
            "sessionUpdate": "agent_thought_chunk",
            "content": {"type": "text", "text": "thinking..."},
        },
        session_id=None,
    )
    assert thought[0]["kind"] == "status"
    assert thought[0]["status"] == "thinking"
    assert thought[0]["message"] == "thinking..."

    # 空文本 chunk 不产生事件。
    assert (
        events_from_acp_update(
            {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": ""}},
            session_id=None,
        )
        == []
    )


def test_events_from_acp_update_tool_lifecycle() -> None:
    call = events_from_acp_update(
        {
            "sessionUpdate": "tool_call",
            "toolCallId": "call-1",
            "title": "bash",
            "status": "in_progress",
            "rawInput": {"command": "ls"},
        },
        session_id="s-1",
    )
    assert call[0]["kind"] == "tool_call"
    assert call[0]["status"] == "started"
    assert call[0]["tool_name"] == "bash"
    assert call[0]["tool_use_id"] == "call-1"
    assert call[0]["input"] == {"command": "ls"}

    result = events_from_acp_update(
        {
            "sessionUpdate": "tool_call_update",
            "toolCallId": "call-1",
            "status": "completed",
            "content": [{"type": "content", "content": {"type": "text", "text": "file.txt"}}],
        },
        session_id="s-1",
    )
    assert result[0]["kind"] == "tool_result"
    assert result[0]["status"] == "success"
    assert result[0]["output"] == "file.txt"

    failed = events_from_acp_update(
        {
            "sessionUpdate": "tool_call_update",
            "toolCallId": "call-2",
            "status": "failed",
            "content": [],
        },
        session_id="s-1",
    )
    assert failed[0]["status"] == "failed"
    assert failed[0]["is_error"] is True

    # 中间状态帧（in_progress / 无状态）不产生终态 tool_result，否则会被误报成功。
    for raw_status in ("in_progress", "pending", None, ""):
        payload: dict[str, Any] = {
            "sessionUpdate": "tool_call_update",
            "toolCallId": "call-3",
        }
        if raw_status is not None:
            payload["status"] = raw_status
        assert events_from_acp_update(payload, session_id=None) == []


def test_events_from_acp_update_usage_and_ignored_kinds() -> None:
    usage = events_from_acp_update(
        {"sessionUpdate": "usage_update", "used": 8100, "size": 262144},
        session_id="s",
    )
    assert usage[0]["kind"] == "status"
    assert usage[0]["status"] == "usage"
    assert usage[0]["usage"] == {"total_tokens": 8100, "context_size": 262144}

    for kind in ("user_message_chunk", "config_option_update", "plan", "plan_update"):
        assert events_from_acp_update({"sessionUpdate": kind}, session_id=None) == []


def test_final_from_prompt_result_stop_reasons() -> None:
    ok = final_from_prompt_result(
        {"stopReason": "end_turn"}, message_text="done", session_id="s", model="m"
    )
    assert ok.is_error is False
    assert ok.result == "done"
    assert ok.subtype == "end_turn"

    cancelled = final_from_prompt_result(
        {"stopReason": "cancelled"}, message_text="partial", session_id="s", model="m"
    )
    assert cancelled.is_error is True
    assert cancelled.subtype == "cancelled"

    refusal = final_from_prompt_result(
        {"stopReason": "refusal"}, message_text="", session_id=None, model=None
    )
    assert refusal.is_error is True


def test_permission_reply_prefers_allow_option() -> None:
    from agent_bridge.agent_runtime.adapters.dsh import _permission_reply

    reply = _permission_reply(
        {
            "options": [
                {"optionId": "reject-once", "kind": "reject_once"},
                {"optionId": "allow-once", "kind": "allow_once"},
                {"optionId": "allow-always", "kind": "allow_always"},
            ]
        }
    )
    assert reply == {"outcome": {"outcome": "selected", "optionId": "allow-once"}}

    fallback = _permission_reply({"options": [{"optionId": "only", "kind": "reject_once"}]})
    assert fallback["outcome"]["optionId"] == "only"

    empty = _permission_reply({"options": []})
    assert empty == {"outcome": {"outcome": "cancelled"}}


# -- Fake ACP 进程驱动的 run 生命周期 --


class _QueuePipe:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[Any] = asyncio.Queue()

    async def readline(self) -> bytes:
        item = await self.queue.get()
        if item is None:
            return b""
        return item

    def emit_eof(self) -> None:
        """模拟进程关闭 stdout（崩溃/提前退出）。"""
        self.queue.put_nowait(None)


class _EmptyPipe:
    def __aiter__(self) -> "_EmptyPipe":
        return self

    async def __anext__(self) -> bytes:
        raise StopAsyncIteration


class _FakeStdin:
    def __init__(self, process: "_FakeAcpProcess") -> None:
        self._process = process
        self.closed = False

    def write(self, data: bytes) -> None:
        line = data.decode("utf-8").strip()
        if not line:
            return
        self._process.requests.append(json.loads(line))
        for message in self._process.handler(json.loads(line)):
            if message == "EOF":
                self._process.stdout.emit_eof()
                continue
            self._process.stdout.queue.put_nowait(
                (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
            )

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


@dataclass
class _FakeAcpProcess:
    handler: Any
    stdout: _QueuePipe
    stderr: _EmptyPipe
    requests: list[dict[str, Any]]
    returncode: int | None = None
    terminated: bool = False
    stdin: Any = None

    async def wait(self) -> int:
        self.returncode = 0
        return 0

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.terminated = True

    @property
    def pid(self) -> int:
        return 424242


class _FakeRuntime:
    """返回最小执行绑定的 resolver 桩；记录收到的 actor/group/model。"""

    def __init__(self, model: str = "deepseek-flash") -> None:
        self.model = model
        self.calls: list[tuple[Any, ...]] = []

    def resolve_execution(self, actor: Any, group: Any, *, model: Any = None) -> Any:
        self.calls.append((actor, group, model))
        identity = SimpleNamespace(
            user="tester",
            uid=os.geteuid(),
            gid=os.getegid(),
            home=Path("/tmp"),
        )
        return SimpleNamespace(
            command="dsh",
            dsh_home=Path("/tmp/.config/dsh/tester"),
            provider="agent-bridge",
            model=model or self.model,
            identity=identity,
            api_key_env={"AGENT_BRIDGE_DSH_API_KEY": "secret"},
            base_url="https://gateway.example/v1",
            process_env=lambda: {
                "DSH_HOME": "/tmp/.config/dsh/tester",
                "AGENT_BRIDGE_DSH_API_KEY": "secret",
            },
        )


def _acp_handler(prompt_updates: list[dict[str, Any]] | None = None) -> Any:
    """按 ACP 语义响应 client 请求的 fake handler。"""
    updates = list(prompt_updates or [])

    def handler(request: dict[str, Any]) -> list[dict[str, Any]]:
        method = request.get("method")
        request_id = request.get("id")
        if method == "initialize":
            return [{"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": 1}}]
        if method == "session/new":
            return [
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"sessionId": "sess-1", "configOptions": []},
                }
            ]
        if method == "session/prompt":
            messages: list[dict[str, Any]] = [
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {"sessionId": "sess-1", "update": update},
                }
                for update in updates
            ]
            messages.append(
                {"jsonrpc": "2.0", "id": request_id, "result": {"stopReason": "end_turn"}}
            )
            return messages
        if method == "session/close":
            return [{"jsonrpc": "2.0", "id": request_id, "result": {}}]
        return [{"jsonrpc": "2.0", "id": request_id, "result": {}}]

    return handler


def _install_fake_process(monkeypatch: pytest.MonkeyPatch, handler: Any) -> _FakeAcpProcess:
    process = _FakeAcpProcess(
        handler=handler,
        stdout=_QueuePipe(),
        stderr=_EmptyPipe(),
        requests=[],
    )
    process.stdin = _FakeStdin(process)  # type: ignore[assignment]

    async def fake_exec(*args: Any, **kwargs: Any) -> _FakeAcpProcess:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return process


def test_dsh_run_streams_updates_and_final(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    handler = _acp_handler(
        [
            {
                "sessionUpdate": "agent_thought_chunk",
                "content": {"type": "text", "text": "plan it"},
            },
            {
                "sessionUpdate": "agent_message_chunk",
                "messageId": "m-1",
                "content": {"type": "text", "text": "HELLO"},
            },
            {
                "sessionUpdate": "agent_message_chunk",
                "messageId": "m-1",
                "content": {"type": "text", "text": "-DSH"},
            },
            {"sessionUpdate": "usage_update", "used": 100, "size": 262144},
        ]
    )
    process = _install_fake_process(monkeypatch, handler)
    runtime = _FakeRuntime()
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "agent-bridge": {
                        "type": "http",
                        "url": "http://127.0.0.1:8765/mcp",
                        "headers": {"X-Agent-Bridge-MetaMCP-Profile": "demo"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    run = DshCodingAgent(runtime=runtime).start(
        _request(tmp_path, mcp_servers=tmp_path / ".mcp.json")
    )

    async def consume() -> list[Any]:
        return [update async for update in run.updates()]

    updates = asyncio.run(consume())

    methods = [item.get("method") for item in process.requests]
    assert methods == [
        "initialize",
        "session/new",
        "session/prompt",
        "session/close",
    ]
    # session/new 携带绝对 cwd 与转换后的 MCP 声明。
    new_session = next(item for item in process.requests if item.get("method") == "session/new")
    assert new_session["params"]["cwd"] == str(tmp_path.resolve())
    assert new_session["params"]["mcpServers"] == [
        {
            "type": "http",
            "name": "agent-bridge",
            "url": "http://127.0.0.1:8765/mcp",
            "headers": [{"name": "X-Agent-Bridge-MetaMCP-Profile", "value": "demo"}],
        }
    ]
    prompt = next(item for item in process.requests if item.get("method") == "session/prompt")
    assert prompt["params"]["sessionId"] == "sess-1"

    # run 目录中生成模型路由 patch。
    patch = yaml.safe_load((tmp_path / MODEL_PATCH_FILENAME).read_text(encoding="utf-8"))
    assert patch == [
        {"id": "acp", "config": {"provider": "agent-bridge", "model": "deepseek-flash"}}
    ]

    kinds = [record["kind"] for update in updates for record in update.events]
    assert kinds == ["status", "agent_message", "agent_message", "status", "result"]
    finals = [update.final for update in updates if update.final is not None]
    assert finals[-1].is_error is False
    # 最终结果由 agent_message_chunk 拼接。
    assert finals[-1].result == "HELLO-DSH"
    assert finals[-1].session_id == "sess-1"
    assert finals[-1].model == "deepseek-flash"
    # run_context 已传递给 resolver。
    assert runtime.calls == [("tester", "grp", None)]


def test_dsh_run_answers_permission_request(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def handler(request: dict[str, Any]) -> list[dict[str, Any]]:
        request_id = request.get("id")
        method = request.get("method")
        if method == "session/new":
            return [
                {"jsonrpc": "2.0", "id": request_id, "result": {"sessionId": "sess-1"}}
            ]
        if method == "session/prompt":
            return [
                {
                    "jsonrpc": "2.0",
                    "id": 9001,
                    "method": "session/request_permission",
                    "params": {
                        "sessionId": "sess-1",
                        "options": [
                            {"optionId": "reject", "kind": "reject_once"},
                            {"optionId": "allow", "kind": "allow_once"},
                        ],
                    },
                },
                {"jsonrpc": "2.0", "id": request_id, "result": {"stopReason": "end_turn"}},
            ]
        if method == "initialize":
            return [{"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": 1}}]
        return [{"jsonrpc": "2.0", "id": request_id, "result": {}}]

    process = _install_fake_process(monkeypatch, handler)
    run = DshCodingAgent(runtime=_FakeRuntime()).start(_request(tmp_path))

    async def consume() -> list[Any]:
        return [update async for update in run.updates()]

    asyncio.run(consume())

    reply = next(
        item
        for item in process.requests
        if isinstance(item.get("id"), int) and "result" in item and "outcome" in item["result"]
    )
    assert reply["result"] == {"outcome": {"outcome": "selected", "optionId": "allow"}}


def test_dsh_run_prompt_error_propagates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def handler(request: dict[str, Any]) -> list[dict[str, Any]]:
        request_id = request.get("id")
        method = request.get("method")
        if method == "session/prompt":
            return [
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32603, "message": "turn failed: no API key"},
                }
            ]
        if method == "session/new":
            return [{"jsonrpc": "2.0", "id": request_id, "result": {"sessionId": "sess-1"}}]
        return [{"jsonrpc": "2.0", "id": request_id, "result": {}}]

    _install_fake_process(monkeypatch, handler)
    run = DshCodingAgent(runtime=_FakeRuntime()).start(_request(tmp_path))

    async def consume() -> None:
        with pytest.raises(Exception, match="no API key"):
            async for _ in run.updates():
                pass

    asyncio.run(consume())


def test_dsh_run_process_exit_fails_pending_prompt_fast(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # DSH 进程在 prompt 执行中崩溃（stdout EOF）：在途 RPC 必须立即失败，
    # 而不是等 86400s 的 prompt 超时兜底把 run 挂近一天。
    def handler(request: dict[str, Any]) -> list[Any]:
        request_id = request.get("id")
        method = request.get("method")
        if method == "initialize":
            return [{"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": 1}}]
        if method == "session/new":
            return [{"jsonrpc": "2.0", "id": request_id, "result": {"sessionId": "sess-1"}}]
        if method == "session/prompt":
            return ["EOF"]  # 模拟进程随即崩溃，不回结算结果
        return [{"jsonrpc": "2.0", "id": request_id, "result": {}}]

    process = _install_fake_process(monkeypatch, handler)
    run = DshCodingAgent(runtime=_FakeRuntime()).start(_request(tmp_path))

    async def consume() -> None:
        with pytest.raises(AcpProcessExitedError, match="进程已退出"):
            async for _ in run.updates():
                pass

    started = time.monotonic()
    asyncio.run(consume())
    assert time.monotonic() - started < 10


def test_dsh_run_abort_sends_cancel_and_closes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    started = asyncio.Event()

    def handler(request: dict[str, Any]) -> list[dict[str, Any]]:
        request_id = request.get("id")
        method = request.get("method")
        if method == "initialize":
            return [{"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": 1}}]
        if method == "session/new":
            return [{"jsonrpc": "2.0", "id": request_id, "result": {"sessionId": "sess-1"}}]
        if method == "session/prompt":
            started.set()
            return []  # 不结算，模拟长任务
        return [{"jsonrpc": "2.0", "id": request_id, "result": {}}]

    process = _install_fake_process(monkeypatch, handler)
    run = DshCodingAgent(runtime=_FakeRuntime()).start(_request(tmp_path))

    async def scenario() -> None:
        async def consume() -> list[Any]:
            return [update async for update in run.updates()]

        task = asyncio.create_task(consume())
        await asyncio.wait_for(started.wait(), timeout=5)
        # 复现 AgentService 的停止路径：取消消费任务，adapter 应先发送
        # session/cancel 再传递取消。
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)

    asyncio.run(scenario())

    notified = [item for item in process.requests if item.get("method") == "session/cancel"]
    assert notified and notified[0]["params"] == {"sessionId": "sess-1"}
    assert process.stdin.closed is True


# -- 执行绑定解析 --


class _StubConfigs:
    def __init__(self, group_config: dict[str, Any] | None, binding: dict[str, Any]) -> None:
        self._group_config = group_config
        self._binding = binding

    def group_config_for_runtime(self, group_key: str) -> dict[str, Any] | None:
        return self._group_config

    def model_binding_for(self, group_key: str) -> dict[str, Any]:
        return self._binding

    def runtime_config_for_runtime(self) -> dict[str, Any]:
        return {"web_command": "dsh web {patch} --host 127.0.0.1 --port {port} --no-open"}


class _StubAccess:
    def actor_group_key(self, actor: str, *, required: bool = False) -> str:
        return "system-maintainers"


def _stub_passwd(home: Path, *, uid: int | None = None, gid: int | None = None):
    def lookup(name: str) -> pwd.struct_passwd:
        return pwd.struct_passwd(
            (
                name,
                "*",
                os.geteuid() if uid is None else uid,
                os.getegid() if gid is None else gid,
                "",
                str(home),
                "/bin/zsh",
            )
        )

    return lookup


def _default_binding() -> dict[str, Any]:
    return {
        "base_url": "https://gateway.example/v1",
        "available_models": ["deepseek-flash", "deepseek-pro"],
        "default_model": "",
        "api_key": "secret",
    }


def test_resolver_builds_binding_and_writes_settings(tmp_path: Path) -> None:
    binding_data = _default_binding()
    binding_data["default_model"] = "deepseek-pro"
    configs = _StubConfigs(
        {"linux_user": "kyynor", "default_model": "deepseek-pro", "api_key": "secret"},
        binding_data,
    )
    resolver = DshAgentRuntimeResolver(configs=configs, access=_StubAccess(), passwd_lookup=_stub_passwd(tmp_path))

    binding = resolver.resolve_execution("kyynor", "system-maintainers")

    assert binding.command == "dsh"
    assert binding.provider == "agent-bridge"
    # 组级默认模型优先于可用列表首项。
    assert binding.model == "deepseek-pro"
    assert binding.api_key_env == {"AGENT_BRIDGE_DSH_API_KEY": "secret"}
    # 用户级 DSH 目录位于 linux home 下的业务用户子目录。
    assert str(binding.dsh_home) == str(tmp_path / ".config" / "dsh" / "kyynor")
    settings = yaml.safe_load((binding.dsh_home / "settings.yaml").read_text(encoding="utf-8"))
    assert settings["llm-pi-ai"]["providers"]["agent-bridge"]["baseURL"] == "https://gateway.example/v1"
    assert settings["agent-default-model"]["model"] == "deepseek-pro"


def test_resolver_chowns_new_dirs_when_root_demotes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # root -> 目标 Linux 用户降权场景：本次新建的目录段（用户目录与全部标准
    # 子目录）都必须归属目标 uid/gid，否则降权后的 DSH 进程无法写入。
    chowned: list[tuple[Path, int, int]] = []
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "chown", lambda path, uid, gid: chowned.append((Path(path), uid, gid)))
    configs = _StubConfigs({"linux_user": "bizuser"}, _default_binding())
    resolver = DshAgentRuntimeResolver(
        configs=configs,
        access=_StubAccess(),
        passwd_lookup=_stub_passwd(tmp_path, uid=12345, gid=543),
    )

    binding = resolver.resolve_execution("kyynor", "system-maintainers")

    assert (binding.dsh_home, 12345, 543) in chowned
    for name in ("sessions", "storages", "change-ledger", "task-board"):
        assert (binding.dsh_home / name, 12345, 543) in chowned
    # 上层新建目录段同样归属目标用户。
    assert (binding.dsh_home.parent, 12345, 543) in chowned
    assert (binding.dsh_home.parent.parent, 12345, 543) in chowned


def test_resolver_skips_chown_when_same_user(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 非降权场景（目标用户即当前用户）不执行 chown，便于开发与测试环境。
    chowned: list[tuple[Path, int, int]] = []
    monkeypatch.setattr(os, "chown", lambda path, uid, gid: chowned.append((Path(path), uid, gid)))
    configs = _StubConfigs({"linux_user": "kyynor"}, _default_binding())
    resolver = DshAgentRuntimeResolver(
        configs=configs,
        access=_StubAccess(),
        passwd_lookup=_stub_passwd(tmp_path),
    )

    binding = resolver.resolve_execution("kyynor", "system-maintainers")

    assert chowned == []
    assert (binding.dsh_home / "sessions").is_dir()


def test_resolver_prefers_model_override_and_validates_it(tmp_path: Path) -> None:
    configs = _StubConfigs({"linux_user": "kyynor"}, _default_binding())
    resolver = DshAgentRuntimeResolver(configs=configs, access=_StubAccess(), passwd_lookup=_stub_passwd(tmp_path))

    binding = resolver.resolve_execution("kyynor", None, model="deepseek-flash")
    assert binding.model == "deepseek-flash"

    from agent_bridge.core.domain import ValidationError

    with pytest.raises(ValidationError, match="不在可用模型列表"):
        resolver.resolve_execution("kyynor", None, model="unknown-model")


def test_resolver_requires_group_config(tmp_path: Path) -> None:
    from agent_bridge.core.domain import ValidationError

    configs = _StubConfigs(None, _default_binding())
    resolver = DshAgentRuntimeResolver(configs=configs, access=_StubAccess(), passwd_lookup=_stub_passwd(tmp_path))

    with pytest.raises(ValidationError, match="尚未配置 DSH"):
        resolver.resolve_execution("kyynor", "system-maintainers")

    with pytest.raises(ValidationError, match="actor"):
        resolver.resolve_execution(None, None)


def test_resolver_rejects_incomplete_model_binding(tmp_path: Path) -> None:
    from agent_bridge.core.domain import ValidationError

    binding = _default_binding()
    binding["base_url"] = ""
    configs = _StubConfigs({"linux_user": "kyynor"}, binding)
    resolver = DshAgentRuntimeResolver(configs=configs, access=_StubAccess(), passwd_lookup=_stub_passwd(tmp_path))

    with pytest.raises(ValidationError, match="模型接入未就绪"):
        resolver.resolve_execution("kyynor", "system-maintainers")
