from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Any

from agent_bridge.agent_runtime.types import CodingAgentRequest


@dataclass
class JsonlCliProcess:
    """管理 JSONL CLI 的通用子进程生命周期。

    各 adapter 仍负责命令参数、事件映射和最终结果语义；这里仅统一进程
    启停、stderr 收集、JSONL 解码以及原生消息回调。
    """

    request: CodingAgentRequest
    args: list[str]
    cwd: str | None = None
    stdin: int | None = None
    forward_native_messages: bool = False
    _process: asyncio.subprocess.Process | None = field(default=None, init=False)
    _stderr_chunks: list[str] = field(default_factory=list, init=False)
    _stderr_task: asyncio.Task[None] | None = field(default=None, init=False)

    async def start(self) -> None:
        if self._process is not None:
            raise RuntimeError("JSONL CLI 子进程已经启动")
        kwargs: dict[str, Any] = {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
        }
        if self.cwd is not None:
            kwargs["cwd"] = self.cwd
        if self.stdin is not None:
            kwargs["stdin"] = self.stdin
        self._process = await asyncio.create_subprocess_exec(*self.args, **kwargs)
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def rows(self) -> AsyncIterator[dict[str, Any]]:
        process = self._require_process()
        if process.stdout is None:
            raise RuntimeError("JSONL CLI 子进程未提供 stdout")
        async for raw_line in process.stdout:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            row = decode_json_line(line)
            if self.forward_native_messages and self.request.on_native_message is not None:
                self.request.on_native_message(row)
            yield row

    async def wait(self) -> int:
        return_code = await self._require_process().wait()
        if self._stderr_task is not None:
            await self._stderr_task
        return return_code

    async def close(self) -> None:
        task = self._stderr_task
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def abort(self, *, timeout_seconds: float = 5) -> None:
        process = self._process
        if process is None or process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
        except TimeoutError:
            process.kill()
            await process.wait()

    def stderr_summary(self, *, limit: int = 2000) -> str:
        return stderr_summary(self._stderr_chunks, limit=limit)

    def _require_process(self) -> asyncio.subprocess.Process:
        if self._process is None:
            raise RuntimeError("JSONL CLI 子进程尚未启动")
        return self._process

    async def _drain_stderr(self) -> None:
        process = self._require_process()
        if process.stderr is None:
            return
        async for raw_line in process.stderr:
            text = raw_line.decode("utf-8", errors="replace")
            self._stderr_chunks.append(text)
            if self.request.stderr is not None:
                self.request.stderr(text)


def effective_prompt(request: CodingAgentRequest) -> str:
    if not request.system_prompt_append:
        return request.prompt
    return f"{request.system_prompt_append}\n\n{request.prompt}"


def stderr_summary(chunks: list[str], *, limit: int = 2000) -> str:
    text = "".join(chunks).strip()
    if len(text) <= limit:
        return text
    return text[-limit:]


def extract_json_object(text: str) -> Any | None:
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


def decode_json_line(line: str) -> dict[str, Any]:
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return {"type": "stdout", "message": line}
    return value if isinstance(value, dict) else {"type": "stdout", "value": value}


def walk_values(value: Any) -> Iterator[Any]:
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from walk_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk_values(item)


def first_string(row: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    for value in walk_values(row):
        if isinstance(value, dict):
            for key in keys:
                item = value.get(key)
                if isinstance(item, str) and item:
                    return item
    return None


def first_value(row: dict[str, Any], *keys: str) -> Any | None:
    """Find the first non-null value for a key at any JSON object depth."""
    for key in keys:
        value = row.get(key)
        if value is not None:
            return value
    for value in walk_values(row):
        if isinstance(value, dict):
            for key in keys:
                item = value.get(key)
                if item is not None:
                    return item
    return None


def first_number(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        if isinstance(value, int | float):
            return float(value)
    return None


def first_int(row: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = row.get(key)
        if isinstance(value, int):
            return value
    return None


def row_is_error(row: dict[str, Any]) -> bool:
    for key in ("is_error", "isError", "error"):
        value = row.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value:
            return True
    status = str(row.get("status") or row.get("state") or "").lower()
    return status in {"error", "failed", "failure"}


def joined_text(parts: list[str]) -> str:
    return "\n".join(part.strip() for part in parts if part.strip()).strip()


def is_generic_final_result(text: str) -> bool:
    return text.lower() in {"", "done", "success", "succeeded", "complete", "completed", "ok"}
