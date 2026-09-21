"""DSH（DeepSeek Harness）Coding Agent 后端。

按 run 启动独立、短生命周期的 ``dsh --profile acp`` 进程，通过标准
Agent Client Protocol（ACP v1，JSON-RPC over stdio）完成一次任务：

- ``initialize`` → ``session/new``（绝对 cwd + MCP 声明）→ ``session/prompt``
  （流式 ``session/update`` 通知）→ ``session/close`` → stdin EOF 进程退出；
- 模型路由经 ``--patch`` 覆盖 ``dsh-acp`` 行的 provider/model（acp profile
  的 shipped 行钉死 deepseek-official，用户 settings.yaml 的默认模型不会
  覆盖它，必须以后到 patch 显式指定）；
- 模型接入（Base URL / 模型目录 / API Key / Linux 身份）复用 DSH Web
  Runtime 的 group 级配置与用户级目录，由
  :class:`agent_bridge.dsh.agent_runtime.DshAgentRuntimeResolver` 解析；
- ``.mcp.json``（Claude 形态）转换为 ACP ``session/new`` 的 stdio/HTTP MCP
  声明，Profile 能力平面照常经 Agent Bridge MetaMCP 网关生效；
- 权限请求（``session/request_permission``）按无人值守语义自动放行
  （优先 allow_* 选项）；沙箱策略仍由 DSH 侧执行。

协议差异（相对 Claude）：无原生 JSON Schema 结构化输出（经 system prompt
回落）、无 USD 成本与 turn 计数、无子代理生命周期事件（subagent 以普通
tool_call 形态出现）、reasoning 阶段经 ``agent_thought_chunk`` 映射为
``status`` 事件。原始 ACP update 全量进 ``messages.jsonl`` 供诊断。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agent_bridge.agent_runtime.adapters.jsonl_cli import effective_prompt as _effective_prompt
from agent_bridge.agent_runtime.events import event_record
from agent_bridge.agent_runtime.types import (
    CodingAgentCapabilities,
    CodingAgentFinal,
    CodingAgentRequest,
    CodingAgentRun,
    CodingAgentUpdate,
)

logger = logging.getLogger(__name__)

# ACP profile 中承载 provider/model 路由的插件行 id（见 dsh-acp-app bundle）。
ACP_ROUTE_ENTRY_ID = "acp"
# run 目录中的模型路由 patch 文件名（一次性运行产物，留在 Agent Bridge run 目录）。
MODEL_PATCH_FILENAME = "dsh-acp-model.patch.yml"

_ACP_PROTOCOL_VERSION = 1
_CLIENT_INFO = {"name": "agent-bridge", "version": "1"}
# prompt 的结算由 AgentService 的外部超时与停止控制兜底，ACP 层不设短超时。
_PROMPT_RPC_TIMEOUT_SECONDS = 86400.0
_STARTUP_TIMEOUT_SECONDS = 60.0
_CLOSE_TIMEOUT_SECONDS = 15.0
# dsh 的 LLM provider 注册可能在 ACP 服务开始应答之后才完成（settings.yaml
# 的托管供应商异步注册）；此窗口内的 ``no adapter registered`` 属于启动竞态，
# 在同一进程上退避重试。
_ADAPTER_WARMUP_RETRIES = 30
_ADAPTER_WARMUP_INTERVAL_SECONDS = 1.0

# session/prompt 结束原因 → 是否错误。
_STOP_REASONS_OK = {"end_turn"}

# tool_call_update 的终态集合；in_progress 等中间状态不产生 tool_result，
# 否则会被误报为成功。
_TERMINAL_TOOL_STATUSES = frozenset({"completed", "failed", "error", "cancelled"})
_TOOL_ERROR_STATUSES = frozenset({"failed", "error", "cancelled"})

# 队列哨兵：prompt 结算任务完成后投入更新队列，结束消费循环。dsh-acp 的
# prompt 只在全部有序 update 送达后结算，因此哨兵之前必是完整事件流。
_SETTLED = object()


class AcpRpcError(RuntimeError):
    """ACP JSON-RPC 错误响应（保留 code/message/data 供诊断）。"""

    def __init__(self, payload: Any) -> None:
        self.payload = payload
        if isinstance(payload, dict):
            message = str(payload.get("message") or payload)
            data = payload.get("data")
            if data:
                message = f"{message} data={json.dumps(data, ensure_ascii=False)[:500]}"
        else:
            message = str(payload)
        super().__init__(message)


class AcpProcessExitedError(RuntimeError):
    """DSH ACP 进程退出（stdout EOF/读取异常），在途 RPC 永远等不到响应。"""


def build_model_patch(*, provider: str, model: str) -> list[dict[str, Any]]:
    """构造覆盖 ``dsh-acp`` 行 provider/model 的 ``--patch`` 文档。"""
    return [
        {
            "id": ACP_ROUTE_ENTRY_ID,
            "config": {"provider": provider, "model": model},
        }
    ]


def write_model_patch(path: Path, *, provider: str, model: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(build_model_patch(provider=provider, model=model), allow_unicode=True),
        encoding="utf-8",
    )
    return path


def acp_mcp_servers(mcp_servers: Path | str | dict[str, Any]) -> list[dict[str, Any]]:
    """把 Claude 形态 MCP 配置（dict / JSON 文件路径）转为 ACP 声明列表。

    HTTP/SSE 条目转 ``{"type": "http", …}``，其余按 stdio 声明转发绝对
    command；无法识别的条目跳过并留痕。``timeout`` 等 ACP 不支持的
    per-server 字段丢弃（工具调用超时由 DSH 侧插件默认值决定）。
    """
    config: Any = mcp_servers
    if isinstance(mcp_servers, Path | str):
        try:
            config = json.loads(Path(mcp_servers).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("DSH 后端读取 MCP 配置失败 path=%s 原因=%s", mcp_servers, exc)
            return []
    if not isinstance(config, dict):
        return []
    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        return []
    declarations: list[dict[str, Any]] = []
    for name, raw in servers.items():
        if not isinstance(raw, dict):
            continue
        server_type = str(raw.get("type") or "").lower()
        if server_type in {"http", "sse", "streamable-http", "remote"}:
            url = str(raw.get("url") or "")
            if not url:
                continue
            headers = [
                {"name": str(key), "value": str(value)}
                for key, value in (raw.get("headers") or {}).items()
            ]
            declarations.append(
                {"type": "http", "name": str(name), "url": url, "headers": headers}
            )
            continue
        command = raw.get("command")
        if not isinstance(command, str) or not command:
            logger.warning("DSH 后端跳过无法转换为 ACP 声明的 MCP 条目 name=%s", name)
            continue
        declaration: dict[str, Any] = {"name": str(name), "command": command}
        if isinstance(raw.get("args"), list):
            declaration["args"] = [str(item) for item in raw["args"]]
        if isinstance(raw.get("env"), dict):
            declaration["env"] = [
                {"name": str(key), "value": str(value)}
                for key, value in raw["env"].items()
            ]
        declarations.append(declaration)
    return declarations


def _dsh_event(kind: str, **values: Any) -> dict[str, Any]:
    return event_record(kind, agent_name="dsh", source="dsh_acp", **values)


def _chunk_text(update: dict[str, Any]) -> str:
    """提取 chunk 类 update 的文本（``content`` 为单块或块列表）。"""
    content = update.get("content")
    if isinstance(content, dict):
        text = content.get("text")
        return text if isinstance(text, str) else ""
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _tool_status(update: dict[str, Any]) -> str:
    status = update.get("status")
    if isinstance(status, str):
        return status
    if isinstance(status, dict):
        return str(status.get("status") or "")
    return ""


def _tool_output_content(update: dict[str, Any]) -> Any:
    """归并 tool_call_update 的 content 列表；纯文本拼接、否则保留原值。"""
    content = update.get("content")
    if not isinstance(content, list):
        return content
    texts: list[str] = []
    for wrapper in content:
        if isinstance(wrapper, dict):
            inner = wrapper.get("content")
            if isinstance(inner, dict) and isinstance(inner.get("text"), str):
                texts.append(inner["text"])
                continue
        return content
    return "\n".join(text for text in texts if text)


def events_from_acp_update(
    update: dict[str, Any], *, session_id: str | None
) -> list[dict[str, Any]]:
    """把一条 ACP ``session/update`` 投影为统一事件流（可能为零或多条）。"""
    kind = str(update.get("sessionUpdate") or "")
    if kind == "agent_message_chunk":
        text = _chunk_text(update)
        if not text:
            return []
        message_id = str(update.get("messageId") or "")
        return [
            _dsh_event(
                "agent_message",
                agent_role="main",
                message=text,
                stream_id=message_id or None,
                partial=True,
                session_id=session_id,
            )
        ]
    if kind == "agent_thought_chunk":
        text = _chunk_text(update)
        if not text:
            return []
        return [
            _dsh_event(
                "status",
                agent_role="main",
                status="thinking",
                message=text,
                session_id=session_id,
            )
        ]
    if kind == "tool_call":
        tool_call_id = str(update.get("toolCallId") or "")
        tool_name = str(update.get("title") or update.get("name") or "unknown")
        return [
            _dsh_event(
                "tool_call",
                agent_role="main",
                status="started",
                tool_name=tool_name,
                tool_use_id=tool_call_id,
                input=update.get("rawInput"),
                message=f"调用工具 {tool_name}",
                session_id=session_id,
            )
        ]
    if kind == "tool_call_update":
        status_value = _tool_status(update)
        if status_value not in _TERMINAL_TOOL_STATUSES:
            # in_progress 等中间帧不产生终态事件（原始 update 仍进 messages.jsonl）。
            return []
        is_error = status_value in _TOOL_ERROR_STATUSES
        return [
            _dsh_event(
                "tool_result",
                agent_role="main",
                status="failed" if is_error else "success",
                tool_use_id=str(update.get("toolCallId") or ""),
                output=_tool_output_content(update),
                is_error=is_error,
                message=f"工具调用{'失败' if is_error else '成功'}",
                session_id=session_id,
            )
        ]
    if kind == "usage_update":
        used = update.get("used")
        size = update.get("size")
        if not isinstance(used, int) or not isinstance(size, int):
            return []
        return [
            _dsh_event(
                "status",
                agent_role="main",
                status="usage",
                message=f"上下文 token 用量 {used}/{size}",
                usage={"total_tokens": used, "context_size": size},
                session_id=session_id,
            )
        ]
    if kind == "compaction_summary_chunk":
        text = _chunk_text(update)
        if not text:
            return []
        return [
            _dsh_event(
                "status",
                agent_role="main",
                status="compaction",
                message=text,
                session_id=session_id,
            )
        ]
    # user_message_chunk / config_option_update / plan* 等对后台 run 无增量语义，
    # 原始 update 仍进 messages.jsonl，不在此重复展开。
    return []


def _join_message_fragments(fragments: list[tuple[str, str]]) -> str:
    """按 messageId 合并消息片段：同 id 直接拼接，不同 id 换行分隔。"""
    if not fragments:
        return ""
    messages: list[str] = []
    current_id: str | None = None
    for message_id, text in fragments:
        if current_id is not None and message_id != current_id:
            messages.append("\n")
        messages.append(text)
        current_id = message_id
    return "".join(messages).strip()


def final_from_prompt_result(
    result: dict[str, Any] | None,
    *,
    message_text: str,
    session_id: str | None,
    model: str | None,
) -> CodingAgentFinal:
    stop_reason = str((result or {}).get("stopReason") or "")
    is_error = bool(stop_reason) and stop_reason not in _STOP_REASONS_OK
    return CodingAgentFinal(
        is_error=is_error,
        result=message_text,
        subtype=stop_reason or None,
        session_id=session_id,
        model=model,
    )


def _permission_reply(params: dict[str, Any]) -> dict[str, Any]:
    """无人值守权限应答：优先 allow_* 选项，其次第一个选项。"""
    options = params.get("options")
    candidates = (
        [item for item in options if isinstance(item, dict)]
        if isinstance(options, list)
        else []
    )
    chosen = next(
        (item for item in candidates if str(item.get("kind") or "").startswith("allow")),
        candidates[0] if candidates else None,
    )
    if chosen is None:
        return {"outcome": {"outcome": "cancelled"}}
    return {"outcome": {"outcome": "selected", "optionId": chosen.get("optionId")}}


def _is_adapter_warmup_error(exc: BaseException) -> bool:
    return isinstance(exc, AcpRpcError) and "no adapter registered" in str(exc)


class _AcpJsonRpc:
    """单个 DSH ACP 进程的 JSON-RPC stdio 客户端（每 run 一个实例）。"""

    def __init__(
        self,
        process: asyncio.subprocess.Process,
        *,
        stderr: Callable[[str], None] | None = None,
    ) -> None:
        self._process = process
        self._stderr_callback = stderr
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.updates: asyncio.Queue[Any] = asyncio.Queue()
        self._stderr_chunks: list[str] = []
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()
        self._closed = False
        self._shutdown_done = False
        self._reader_dead = False

    @property
    def process(self) -> asyncio.subprocess.Process:
        return self._process

    def start_background_tasks(self) -> None:
        self._reader_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def _read_loop(self) -> None:
        stdout = self._process.stdout
        if stdout is None:
            self._fail_pending("DSH ACP 进程未提供 stdout")
            return
        try:
            while True:
                raw = await stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("DSH ACP 收到非 JSON 行 line=%s", line[:200])
                    continue
                if not isinstance(message, dict):
                    continue
                if "id" in message and ("result" in message or "error" in message):
                    future = self._pending.pop(int(message["id"]), None)
                    if future is not None and not future.done():
                        if "error" in message:
                            future.set_exception(AcpRpcError(message["error"]))
                        else:
                            future.set_result(message["result"])
                    continue
                method = message.get("method")
                if method == "session/update":
                    params = message.get("params")
                    if isinstance(params, dict):
                        await self.updates.put(params)
                elif method == "session/request_permission":
                    params = message.get("params")
                    if isinstance(params, dict):
                        await self.reply(message.get("id"), _permission_reply(params))
                elif method is not None and message.get("id") is not None:
                    # 其他 server→client 请求统一回空结果，避免对端挂起。
                    await self.reply(message.get("id"), {})
        except asyncio.CancelledError:
            # shutdown 主动取消读取任务（正常收尾/取消路径），不是进程故障。
            raise
        except Exception as exc:
            logger.exception("DSH ACP 读取循环异常退出")
            self._fail_pending(f"DSH ACP 读取循环异常：{exc}")
            return
        # stdout EOF：进程已退出（崩溃或提前结束）。必须让在途 RPC 立即失败，
        # 否则 prompt 的 86400s 超时兜底会让 run 挂近一天。
        self._fail_pending(
            f"DSH ACP 进程已退出（code={self._process.returncode}），在途 RPC 无法完成"
        )

    def _fail_pending(self, reason: str) -> None:
        """让全部在途 RPC future 立即失败，并标记读取通道已死。"""
        self._reader_dead = True
        for future in self._pending.values():
            if not future.done():
                future.set_exception(AcpProcessExitedError(reason))
        self._pending.clear()

    async def _drain_stderr(self) -> None:
        stderr = self._process.stderr
        if stderr is None:
            return
        try:
            async for raw in stderr:
                text = raw.decode("utf-8", errors="replace")
                self._stderr_chunks.append(text)
                if self._stderr_callback is not None:
                    self._stderr_callback(text)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("DSH ACP stderr 读取异常退出")

    async def reply(self, request_id: Any, result: dict[str, Any]) -> None:
        if request_id is None:
            return
        await self._write({"jsonrpc": "2.0", "id": request_id, "result": result})

    async def call(self, method: str, params: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        if self._reader_dead or self._closed:
            # 读取通道已死或已 shutdown：新 RPC 永远等不到响应，立即失败
            # 而不是等到 timeout（session/close 收尾路径因此不会被拖住）。
            raise AcpProcessExitedError("DSH ACP 进程已退出，无法发送 RPC")
        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._write(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params,
                }
            )
            return await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(request_id, None)

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        await self._write({"jsonrpc": "2.0", "method": method, "params": params})

    async def _write(self, payload: dict[str, Any]) -> None:
        if self._closed or self._process.stdin is None:
            raise RuntimeError("DSH ACP 进程标准输入已关闭")
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        async with self._write_lock:
            self._process.stdin.write(data)
            await self._process.stdin.drain()

    async def next_update(self) -> Any:
        return await self.updates.get()

    def stderr_summary(self, *, limit: int = 2000) -> str:
        text = "".join(self._stderr_chunks).strip()
        return text[-limit:] if len(text) > limit else text

    async def shutdown(self) -> None:
        """关闭 stdin（ACP 规定 stdin EOF 即优雅退出），超时升级 SIGTERM/SIGKILL。

        幂等：abort 与 updates 的收尾路径都会调用。
        """
        if self._shutdown_done:
            await self._wait_process()
            return
        self._shutdown_done = True
        self._closed = True
        stdin = self._process.stdin
        if stdin is not None:
            with contextlib.suppress(RuntimeError, ConnectionResetError, BrokenPipeError):
                stdin.close()
        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
        await self._wait_process()

    async def _wait_process(self) -> None:
        if self._process.returncode is not None:
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._process.wait(), timeout=_CLOSE_TIMEOUT_SECONDS)
            return
        with contextlib.suppress(ProcessLookupError):
            self._process.terminate()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._process.wait(), timeout=_CLOSE_TIMEOUT_SECONDS)
            return
        with contextlib.suppress(ProcessLookupError):
            self._process.kill()
        await self._process.wait()

    async def cancel_tasks(self) -> None:
        for task in (self._reader_task, self._stderr_task):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task


@dataclass
class _DshRun:
    request: CodingAgentRequest
    command: str
    model: str | None
    runtime: Any = None

    _client: _AcpJsonRpc | None = field(default=None, init=False)
    _session_id: str | None = field(default=None, init=False)

    async def updates(self) -> AsyncIterator[CodingAgentUpdate]:
        binding = self._resolve_binding()
        prompt = _effective_prompt(self.request)
        mcp_declarations = acp_mcp_servers(self.request.mcp_servers)
        patch_path = write_model_patch(
            self.request.cwd / MODEL_PATCH_FILENAME,
            provider=binding.provider,
            model=binding.model,
        )
        client = await self._spawn(binding, patch_path, mcp_declarations)
        self._client = client
        # (messageId, text) 片段：同一 messageId 的 chunk 属同一条 assistant
        # 消息，直接拼接；不同消息以换行分隔。
        message_fragments: list[tuple[str, str]] = []
        final: CodingAgentFinal | None = None

        async def _settle() -> dict[str, Any]:
            try:
                return await self._call_with_warmup_retry(
                    client,
                    "session/prompt",
                    {
                        "sessionId": self._session_id,
                        "prompt": [{"type": "text", "text": prompt}],
                    },
                    timeout=_PROMPT_RPC_TIMEOUT_SECONDS,
                )
            finally:
                await client.updates.put(_SETTLED)

        settle_task: asyncio.Task[dict[str, Any]] | None = None
        try:
            await client.call(
                "initialize",
                {
                    "protocolVersion": _ACP_PROTOCOL_VERSION,
                    "clientCapabilities": {},
                    "clientInfo": _CLIENT_INFO,
                },
                timeout=_STARTUP_TIMEOUT_SECONDS,
            )
            new_session = await self._call_with_warmup_retry(
                client,
                "session/new",
                {
                    "cwd": str(Path(self.request.cwd).resolve()),
                    "mcpServers": mcp_declarations,
                },
                timeout=_STARTUP_TIMEOUT_SECONDS,
            )
            self._session_id = str(new_session.get("sessionId") or "") or None
            settle_task = asyncio.create_task(_settle())
            while True:
                item = await client.next_update()
                if item is _SETTLED:
                    break
                update = item.get("update") if isinstance(item, dict) else None
                if not isinstance(update, dict):
                    continue
                if str(update.get("sessionUpdate") or "") == "agent_message_chunk":
                    text = _chunk_text(update)
                    if text:
                        message_fragments.append(
                            (str(update.get("messageId") or ""), text)
                        )
                events = events_from_acp_update(update, session_id=self._session_id)
                if events:
                    yield CodingAgentUpdate(raw=update, events=events)
            prompt_result = await settle_task
            settle_task = None
            final = final_from_prompt_result(
                prompt_result,
                message_text=_join_message_fragments(message_fragments),
                session_id=self._session_id,
                model=binding.model,
            )
            yield CodingAgentUpdate(
                events=[
                    _dsh_event(
                        "result",
                        agent_role="main",
                        status="failed" if final.is_error else "success",
                        message=final.result or final.subtype or "done",
                        subtype=final.subtype,
                        session_id=self._session_id,
                    )
                ],
                final=final,
            )
        except asyncio.CancelledError:
            # 消费侧取消（用户停止/超时）：先走 ACP 正规取消，让 prompt 以
            # cancelled 结算、进程随 stdin EOF 自行退出，再向调用方传递取消。
            with contextlib.suppress(BaseException):
                await self.abort()
            raise
        finally:
            if settle_task is not None:
                settle_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await settle_task
            if self._session_id is not None:
                with contextlib.suppress(Exception):
                    await client.call(
                        "session/close",
                        {"sessionId": self._session_id},
                        timeout=_CLOSE_TIMEOUT_SECONDS,
                    )
            await client.cancel_tasks()
            await client.shutdown()
        if final is None:
            # updates() 未经 prompt 结算即退出（如进程崩溃）；补错误终态。
            stderr_tail = client.stderr_summary(limit=600)
            message = f"dsh acp 进程提前退出（code={client.process.returncode}）"
            if stderr_tail:
                message = f"{message}：{stderr_tail}"
            yield CodingAgentUpdate(
                events=[_dsh_event("error", status="failed", message=message)],
                final=CodingAgentFinal(
                    is_error=True, result=message, session_id=self._session_id
                ),
            )

    async def _call_with_warmup_retry(
        self,
        client: _AcpJsonRpc,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        """对供应商注册竞态做退避重试（同一进程上重试，不重启进程）。"""
        for attempt in range(_ADAPTER_WARMUP_RETRIES):
            try:
                return await client.call(method, params, timeout=timeout)
            except AcpRpcError as exc:
                if not _is_adapter_warmup_error(exc) or attempt == _ADAPTER_WARMUP_RETRIES - 1:
                    raise
                if attempt == 0:
                    logger.info(
                        "DSH ACP 供应商注册未就绪，退避重试 method=%s 原因=%s",
                        method,
                        exc,
                    )
                await asyncio.sleep(_ADAPTER_WARMUP_INTERVAL_SECONDS)
        raise RuntimeError("unreachable")

    async def abort(self) -> None:
        client = self._client
        if client is None:
            return
        if self._session_id is not None:
            with contextlib.suppress(Exception):
                await client.notify("session/cancel", {"sessionId": self._session_id})
        await client.shutdown()

    def _resolve_binding(self) -> Any:
        runtime = self.runtime
        if runtime is None:
            raise RuntimeError(
                "DSH 后端未接入运行时依赖（dsh_runtime），无法解析组级模型接入配置"
            )
        context = self.request.run_context
        return runtime.resolve_execution(
            context.actor if context is not None else None,
            context.owner_group_key if context is not None else None,
            model=self.request.model or self.model,
        )

    async def _spawn(
        self, binding: Any, patch_path: Path, mcp_declarations: list[dict[str, Any]]
    ) -> _AcpJsonRpc:
        from agent_bridge.dsh.launcher import demotion_kwargs

        env = {**os.environ, **binding.process_env()}
        process = await asyncio.create_subprocess_exec(
            binding.command,
            "--profile",
            "acp",
            "--patch",
            str(patch_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.request.cwd),
            env=env,
            **demotion_kwargs(binding.identity),
        )
        client = _AcpJsonRpc(process, stderr=self.request.stderr)
        client.start_background_tasks()
        logger.info(
            "DSH ACP 子进程已启动 pid=%s cwd=%s model=%s mcp=%d patch=%s",
            process.pid,
            self.request.cwd,
            binding.model,
            len(mcp_declarations),
            patch_path,
        )
        return client


class DshCodingAgent:
    source = "dsh_acp"
    # DSH 的模型路由固定走 Agent Bridge 托管供应商（openai-completions 网关），
    # 当前路由不暴露 reasoning effort 选项，配置 effort 会被 registry 明确拒绝。
    supported_efforts: frozenset[str] = frozenset()
    capabilities = CodingAgentCapabilities(
        supports_mcp=True,
        supports_native_json_schema=False,
        supports_skills=False,
        supports_subagents=False,
        supports_cost=False,
        supports_turn_count=False,
        supports_abort=True,
        supports_partial_messages=True,
    )

    def __init__(
        self,
        *,
        backend_key: str = "dsh",
        command: str = "dsh",
        model: str | None = None,
        runtime: Any = None,
    ) -> None:
        self.backend_key = backend_key
        self.display_name = "DSH"
        self.command = command
        self.model = model
        self.runtime = runtime

    def start(self, request: CodingAgentRequest) -> CodingAgentRun:
        return _DshRun(
            request=request,
            command=self.command,
            model=self.model,
            runtime=self.runtime,
        )
