"""OpenCode server 进程与 HTTP 生命周期管理。"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx


_LISTENING_URL = re.compile(r"https?://[^\s\x1b]+")


class OpenCodeServerError(RuntimeError):
    """OpenCode server 启动或 HTTP 调用失败。"""


@dataclass
class OpenCodeServerProcess:
    """为一次 Agent run 启动并回收一个 OpenCode server。"""

    command: str = "opencode"
    hostname: str = "127.0.0.1"
    startup_timeout: float = 20.0
    _process: asyncio.subprocess.Process | None = field(default=None, init=False)
    _client: httpx.AsyncClient | None = field(default=None, init=False)
    _stdout_task: asyncio.Task[None] | None = field(default=None, init=False)
    _stderr_task: asyncio.Task[None] | None = field(default=None, init=False)
    _ready: asyncio.Event | None = field(default=None, init=False)
    _base_url: str | None = field(default=None, init=False)
    _stdout_lines: list[str] = field(default_factory=list, init=False)
    _stderr_lines: list[str] = field(default_factory=list, init=False)

    async def start(self, *, cwd: Path) -> None:
        """启动 server，等待监听地址出现并确认 HTTP 已可访问。"""
        if self._process is not None:
            raise RuntimeError("OpenCode server 已经启动")
        self._ready = asyncio.Event()
        try:
            self._process = await asyncio.create_subprocess_exec(
                self.command,
                "serve",
                "--port",
                "0",
                "--hostname",
                self.hostname,
                cwd=str(cwd),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise OpenCodeServerError(f"启动 OpenCode server 失败: {exc}") from exc

        self._stdout_task = asyncio.create_task(self._drain_stdout())
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=self.startup_timeout)
        except TimeoutError as exc:
            raise OpenCodeServerError(
                f"OpenCode server 启动超时（{self.startup_timeout:.0f}s）: {self.stderr_summary()}"
            ) from exc
        if self._process.returncode is not None:
            raise OpenCodeServerError(
                f"OpenCode server 提前退出 status={self._process.returncode}: {self.stderr_summary()}"
            )
        if not self._base_url:
            raise OpenCodeServerError(
                f"OpenCode server 未报告监听地址: {' '.join(self._stdout_lines[-5:])}"
            )

        password = os.environ.get("OPENCODE_SERVER_PASSWORD")
        auth = None
        if password:
            auth = httpx.BasicAuth(
                os.environ.get("OPENCODE_SERVER_USERNAME", "opencode"),
                password,
            )
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            auth=auth,
            timeout=httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0),
        )
        await self._wait_until_http_ready()

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        """向 OpenCode server 发起 JSON 请求并统一转换 HTTP 错误。"""
        if self._client is None:
            raise RuntimeError("OpenCode server 尚未启动")
        try:
            response = await self._client.request(method, path, params=params, json=payload)
        except httpx.HTTPError as exc:
            raise OpenCodeServerError(f"OpenCode server 请求失败: {exc}") from exc
        if response.is_error:
            detail = response.text.strip()
            if len(detail) > 2000:
                detail = detail[-2000:]
            raise OpenCodeServerError(
                f"OpenCode server HTTP {response.status_code} {method.upper()} {path}: {detail}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise OpenCodeServerError(
                f"OpenCode server 返回非 JSON: {method.upper()} {path}"
            ) from exc

    async def request_no_content(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """发送不需要 JSON 响应体的请求，例如 ``prompt_async``。"""
        if self._client is None:
            raise RuntimeError("OpenCode server 尚未启动")
        try:
            response = await self._client.request(method, path, params=params, json=payload)
        except httpx.HTTPError as exc:
            raise OpenCodeServerError(f"OpenCode server 请求失败: {exc}") from exc
        if response.is_error:
            detail = response.text.strip()
            if len(detail) > 2000:
                detail = detail[-2000:]
            raise OpenCodeServerError(
                f"OpenCode server HTTP {response.status_code} {method.upper()} {path}: {detail}"
            )

    async def stream_json_events(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
    ) -> AsyncIterator[Any]:
        """读取 OpenCode SSE，并逐条解码 ``data:`` 中的 JSON。

        这里只处理通用 SSE framing，不解释 OpenCode 的事件类型。事件协议
        映射留在 coding-agent adapter，未来替换 v2 client 时不会影响进程
        生命周期和网络流管理。
        """
        if self._client is None:
            raise RuntimeError("OpenCode server 尚未启动")
        try:
            async with self._client.stream(
                "GET",
                path,
                params=params,
                headers={"Accept": "text/event-stream"},
            ) as response:
                if response.is_error:
                    await response.aread()
                    detail = response.text.strip()
                    if len(detail) > 2000:
                        detail = detail[-2000:]
                    raise OpenCodeServerError(
                        f"OpenCode server HTTP {response.status_code} GET {path}: {detail}"
                    )

                data_lines: list[str] = []
                async for line in response.aiter_lines():
                    if line == "":
                        payload = _decode_sse_data(data_lines, path=path)
                        data_lines = []
                        if payload is not None:
                            yield payload
                        continue
                    if line.startswith(":"):
                        continue
                    if line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                    # OpenCode currently only needs data. Ignore standard SSE
                    # event/id/retry fields so the parser remains tolerant of
                    # future server metadata.

                payload = _decode_sse_data(data_lines, path=path)
                if payload is not None:
                    yield payload
        except httpx.HTTPError as exc:
            raise OpenCodeServerError(f"OpenCode server SSE 请求失败: {exc}") from exc

    async def close(self) -> None:
        """关闭 HTTP client，并终止本次 run 创建的 server 进程。"""
        client = self._client
        self._client = None
        if client is not None:
            await client.aclose()

        process = self._process
        self._process = None
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except TimeoutError:
                process.kill()
                await process.wait()

        for task in (self._stdout_task, self._stderr_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._stdout_task = None
        self._stderr_task = None

    async def abort(self) -> None:
        """中止请求并回收 server。"""
        await self.close()

    def stderr_summary(self, *, limit: int = 2000) -> str:
        text = "".join(self._stderr_lines).strip()
        if len(text) <= limit:
            return text
        return text[-limit:]

    async def _wait_until_http_ready(self) -> None:
        if self._client is None:
            raise RuntimeError("OpenCode HTTP client 尚未创建")
        deadline = asyncio.get_running_loop().time() + min(self.startup_timeout, 10.0)
        last_error: Exception | None = None
        while asyncio.get_running_loop().time() < deadline:
            try:
                response = await self._client.get("/global/health")
                if response.status_code < 500:
                    return
            except httpx.HTTPError as exc:
                last_error = exc
            await asyncio.sleep(0.05)
        detail = f": {last_error}" if last_error else ""
        raise OpenCodeServerError(f"OpenCode server HTTP 未就绪{detail}")

    async def _drain_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        async for raw_line in process.stdout:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if line:
                self._stdout_lines.append(line)
                self._stdout_lines = self._stdout_lines[-50:]
                match = _LISTENING_URL.search(line)
                if match and self._base_url is None:
                    self._base_url = match.group(0).rstrip(".,)")
                    if self._ready is not None:
                        self._ready.set()
        if self._ready is not None and not self._ready.is_set():
            self._ready.set()

    async def _drain_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        async for raw_line in process.stderr:
            self._stderr_lines.append(raw_line.decode("utf-8", errors="replace"))
            self._stderr_lines = self._stderr_lines[-50:]


def _decode_sse_data(data_lines: list[str], *, path: str) -> Any | None:
    if not data_lines:
        return None
    raw = "\n".join(data_lines)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OpenCodeServerError(f"OpenCode server SSE 返回非 JSON: GET {path}") from exc
