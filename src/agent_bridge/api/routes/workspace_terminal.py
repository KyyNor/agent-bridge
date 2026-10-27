"""个人网页终端入口：同源、登录与当前归属校验后代理已登记的 PTY。"""
from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, Query, Request, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, WebSocketException

from agent_bridge.core.domain import AccessDenied, AgentBridgeError, ValidationError
from agent_bridge.agent_workspace.file_worker import MAX_FILE_BYTES, valid_filename

logger = logging.getLogger(__name__)


class TerminalStartRequest(BaseModel):
    directory: str = Field(min_length=1, max_length=4096)
    profile_key: str | None = Field(default=None, max_length=255)


def upstream_close_code(upstream) -> int:
    """上游关闭码归一到浏览器可判定的取值；1005/1006/1015 视为链路异常（1011）。"""
    code = upstream.close_code or 1011
    return 1011 if code in {1005, 1006, 1015} else code


def require_same_origin(request: Request):
    origin = request.headers.get('origin')
    if origin:
        parsed = urlsplit(origin)
        if parsed.scheme != request.url.scheme or parsed.netloc != request.headers.get('host'):
            raise AccessDenied('拒绝跨站终端操作')
    if request.headers.get('sec-fetch-site') == 'cross-site':
        raise AccessDenied('拒绝跨站终端操作')


def create_terminal_routes(service, identity_resolver):
    router = APIRouter(prefix="/agent-workspace")

    def actor(request: Request):
        return identity_resolver.resolve(request).user_id

    @router.get('/terminal/{agent}/directories')
    def directories(agent: str, path: str | None = Query(default=None, max_length=4096),
                    user_id: str = Depends(actor)):
        return service.workspace_runtime(agent, mode='terminal').browse_directories(user_id, path)

    @router.post('/terminal/{agent}/start')
    def start(agent: str, payload: TerminalStartRequest, request: Request, user_id: str = Depends(actor)):
        require_same_origin(request)
        return service.workspace_runtime(agent, mode='terminal').start_in_directory(
            user_id, directory=payload.directory, profile_key=payload.profile_key,
        )

    @router.get('/terminal/{agent}/files')
    def files(agent: str, path: str = Query(default='', max_length=4096), user_id: str = Depends(actor)):
        return service.workspace_runtime(agent, mode='terminal').files(user_id, 'list', path=path)

    @router.get('/terminal/{agent}/download')
    def download(agent: str, path: str = Query(max_length=4096), user_id: str = Depends(actor)):
        data = service.workspace_runtime(agent, mode='terminal').files(user_id, 'download', path=path)
        filename = quote(path.rsplit('/', 1)[-1], safe='')
        return Response(data, media_type='application/octet-stream', headers={
            'Content-Disposition': "attachment; filename*=UTF-8''" + filename,
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
        })

    @router.post('/terminal/{agent}/upload')
    async def upload(agent: str, request: Request, name: str = Query(max_length=255),
                     path: str = Query(default='', max_length=4096), user_id: str = Depends(actor)):
        require_same_origin(request)
        if request.headers.get('content-type', '').split(';')[0] != 'application/octet-stream':
            raise ValidationError('上传必须使用二进制文件请求')
        try:
            valid_filename(name)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        runtime = service.workspace_runtime(agent, mode='terminal')
        await asyncio.to_thread(runtime.terminal_target, user_id, touch=False)
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > MAX_FILE_BYTES:
                raise ValidationError('单个文件不能超过 32 MiB')
            data.extend(chunk)
        return await asyncio.to_thread(runtime.files, user_id, 'upload', path=path, name=name, data=bytes(data))

    @router.websocket("/terminal/{agent}")
    async def terminal(socket: WebSocket, agent: str):
        # Cookie 认证的 WebSocket 必须校验 Origin，防止跨站劫持终端。
        origin = urlsplit(socket.headers.get("origin", ""))
        expected_scheme = "https" if socket.url.scheme == "wss" else "http"
        if origin.scheme != expected_scheme or origin.netloc != socket.headers.get("host"):
            await socket.close(code=1008)
            return
        try:
            request = Request({**socket.scope, "type": "http", "method": "GET"})
            user_id = identity_resolver.resolve(request).user_id
        except AgentBridgeError:
            await socket.close(code=1008)
            return
        try:
            runtime = service.workspace_runtime(agent, mode="terminal")
            target, secret = await asyncio.to_thread(runtime.terminal_target, user_id)
        except AgentBridgeError as exc:
            # 终端已结束/已被回收/归属或能力平面变化：与登录失效区分开，
            # 前端据此提示“关闭页面”而不是“检查登录状态”。
            logger.info("终端代理目标不可用 agent=%s user=%s 原因=%s", agent, user_id, exc)
            await socket.close(code=4001, reason="终端已结束，请重新进入")
            return
        await socket.accept()
        url = target.replace("http://", "ws://", 1) + "/terminal"
        try:
            async with connect(url, additional_headers={"Authorization": "Bearer " + secret},
                               max_size=1024 * 1024, open_timeout=10, close_timeout=2) as upstream:
                async def to_browser():
                    try:
                        async for data in upstream:
                            if isinstance(data, bytes):
                                await socket.send_bytes(data)
                            else:
                                await socket.send_text(data)
                    except ConnectionClosed:
                        # 4009（连接独占）与 4000（Agent 退出）必须原样到浏览器，
                        # 不能一律转为 1011 触发无意义的自动重连。
                        pass
                    code = upstream_close_code(upstream)
                    if code not in {4000, 4009}:
                        logger.info("终端代理上游关闭 agent=%s user=%s code=%s reason=%s",
                                    agent, user_id, code, upstream.close_reason or "")
                    with suppress(RuntimeError):
                        await socket.close(code=code, reason=upstream.close_reason or "")

                async def to_agent():
                    import json
                    try:
                        while True:
                            data = await socket.receive_text()
                            if len(data.encode()) > 128 * 1024:
                                await socket.close(code=1009)
                                return
                            # 每次输入重新校验组归属与 Profile；地址和秘密从不接受客户端指定。
                            try:
                                message = json.loads(data)
                            except ValueError:
                                await socket.close(code=1008)
                                return
                            touch = isinstance(message, dict) and message.get("type") == "input"
                            await asyncio.to_thread(runtime.terminal_target, user_id, touch=touch)
                            await upstream.send(data)
                    except ConnectionClosed:
                        # 上游已关闭：用真实关闭码收尾，避免 1011/1000 抢先被当成网络抖动。
                        code = upstream_close_code(upstream)
                        logger.info("终端代理上游关闭 agent=%s user=%s code=%s", agent, user_id, code)
                        with suppress(RuntimeError):
                            await socket.close(code=code, reason=upstream.close_reason or "")
                        return

                async def guard():
                    while True:
                        await asyncio.sleep(20)
                        await asyncio.to_thread(runtime.terminal_target, user_id, touch=False)

                tasks = [asyncio.create_task(fn()) for fn in (to_browser, to_agent, guard)]
                try:
                    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        task.result()
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        except (WebSocketDisconnect, WebSocketException, OSError, RuntimeError, AgentBridgeError) as exc:
            # 归属/能力平面在会话中途失效时终端已不可继续，按“已结束”处理而不是登录失效。
            code = 4001 if isinstance(exc, AgentBridgeError) else 1011
            logger.info("终端代理连接结束 agent=%s user=%s 状态=%s code=%s",
                        agent, user_id, type(exc).__name__, code)
            with suppress(RuntimeError, WebSocketDisconnect, OSError):
                await socket.close(code=code)

    return router
