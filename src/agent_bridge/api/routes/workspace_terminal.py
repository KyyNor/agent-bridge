"""个人网页终端入口：同源、登录与当前归属校验后代理已登记的 PTY。"""
from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from urllib.parse import urlsplit

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, WebSocketException

from agent_bridge.core.domain import AgentBridgeError

logger = logging.getLogger(__name__)


def create_terminal_routes(service, identity_resolver):
    router = APIRouter(prefix="/agent-workspace")

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
            runtime = service.workspace_runtime(agent, mode="terminal")
            target, secret = await asyncio.to_thread(runtime.terminal_target, user_id)
        except AgentBridgeError:
            await socket.close(code=1008)
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
                    code = upstream.close_code or 1011
                    if code in {1005, 1006, 1015}:
                        code = 1011
                    await socket.close(code=code, reason=upstream.close_reason or "")

                async def to_agent():
                    while True:
                        data = await socket.receive_text()
                        if len(data.encode()) > 128 * 1024:
                            await socket.close(code=1009)
                            return
                        # 每次输入重新校验组归属与 Profile；地址和秘密从不接受客户端指定。
                        import json
                        try:
                            message = json.loads(data)
                        except ValueError:
                            await socket.close(code=1008)
                            return
                        touch = isinstance(message, dict) and message.get("type") == "input"
                        await asyncio.to_thread(runtime.terminal_target, user_id, touch=touch)
                        await upstream.send(data)

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
            logger.info("终端代理连接结束 agent=%s user=%s 状态=%s", agent, user_id, type(exc).__name__)
            with suppress(RuntimeError, WebSocketDisconnect):
                await socket.close(code=1008 if isinstance(exc, AgentBridgeError) else 1011)

    return router
