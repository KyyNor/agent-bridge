"""目标 Linux 用户下的 PTY/WS bridge；生命周期由 WorkspaceRuntimeService 托管。"""
from __future__ import annotations

import argparse
import asyncio
import errno
import fcntl
import json
import logging
import os
import pty
import secrets
import signal
import struct
import subprocess
import sys
import termios
import time
from collections import deque
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
AUTH_ENV = "AGENT_BRIDGE_TERMINAL_AUTH"
DISCONNECT_GRACE_SECONDS = 120
REPLAY_BYTES = 512 * 1024
MAX_INPUT_BYTES = 64 * 1024


class PtySession:
    """单个 Agent PTY：限量重放、独占连接、断线宽限与有界输出队列。"""

    def __init__(self, command: list[str]):
        self.command = command
        self.process: subprocess.Popen | None = None
        self.master = -1
        self.output: deque[bytes] = deque()
        self.output_bytes = 0
        self.queue: asyncio.Queue | None = None
        self.disconnected_at = time.monotonic()
        self.finished = asyncio.Event()
        self.exit_code: int | None = None

    def start(self):
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
        env = dict(os.environ)
        env.pop(AUTH_ENV, None)
        try:
            self.process = subprocess.Popen(
                [sys.executable, "-m", "agent_bridge.agent_workspace.terminal_server", "--child", *self.command],
                stdin=slave, stdout=slave, stderr=slave, env=env, close_fds=True,
            )
        except BaseException:
            os.close(self.master)
            self.master = -1
            raise
        finally:
            os.close(slave)
        os.set_blocking(self.master, False)
        asyncio.get_running_loop().add_reader(self.master, self.read_output)
        logger.info("终端 Agent 已启动 pid=%s", self.process.pid)

    def read_output(self):
        try:
            data = os.read(self.master, 16384)
        except BlockingIOError:
            return
        except OSError as exc:
            if exc.errno != errno.EIO:
                logger.warning("终端 PTY 读取失败 pid=%s 原因=%s", self.process.pid, exc)
            data = b""
        if not data:
            asyncio.get_running_loop().remove_reader(self.master)
            self.finished.set()
            return
        self.output.append(data)
        self.output_bytes += len(data)
        while self.output_bytes > REPLAY_BYTES:
            self.output_bytes -= len(self.output.popleft())
        if self.queue is not None:
            try:
                self.queue.put_nowait(data)
            except asyncio.QueueFull:
                # 慢连接断开后可重连；不能让浏览器无限拖住 Agent 或占满内存。
                self.queue = None
                self.disconnected_at = time.monotonic()

    async def write_input(self, data: bytes):
        view = memoryview(data)
        while view:
            try:
                written = os.write(self.master, view)
                view = view[written:]
            except BlockingIOError:
                await asyncio.sleep(0.01)

    def resize(self, cols: int, rows: int):
        if not (2 <= cols <= 500 and 2 <= rows <= 300):
            raise ValueError("终端尺寸超出允许范围")
        fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    async def stop(self):
        if self.master >= 0:
            asyncio.get_running_loop().remove_reader(self.master)
        if self.process and self.process.poll() is None:
            # 子进程是独立会话；通用 runtime 另有后代树与环境标记完整回收兜底。
            with suppress(ProcessLookupError):
                os.killpg(self.process.pid, signal.SIGTERM)
            try:
                await asyncio.to_thread(self.process.wait, 3)
            except subprocess.TimeoutExpired:
                with suppress(ProcessLookupError):
                    os.killpg(self.process.pid, signal.SIGKILL)
                await asyncio.to_thread(self.process.wait, 3)
        if self.master >= 0:
            os.close(self.master)
            self.master = -1


def create_terminal_app(command: list[str], secret: str, *, grace_seconds: float = DISCONNECT_GRACE_SECONDS):
    session = PtySession(command)

    @asynccontextmanager
    async def lifespan(app):
        session.start()
        async def monitor():
            while True:
                await asyncio.sleep(1)
                exited = session.process.poll() is not None
                expired = session.queue is None and time.monotonic() - session.disconnected_at >= grace_seconds
                if exited or expired:
                    session.exit_code = session.process.poll()
                    session.finished.set()
                    if expired:
                        logger.info("终端断线宽限到期，回收 pid=%s", session.process.pid)
                    # 让 uvicorn 正常结束，服务侧状态探测即可识别退出。
                    server = getattr(app.state, "server", None)
                    if server is not None:
                        server.should_exit = True
                    return
        task = asyncio.create_task(monitor())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            await session.stop()

    app = FastAPI(lifespan=lifespan)
    app.state.session = session

    def authorized(headers):
        return bool(secret) and secrets.compare_digest(headers.get("authorization", ""), "Bearer " + secret)

    @app.get("/health")
    def health(request: Request):
        if not authorized(request.headers):
            return JSONResponse({"error": "未授权"}, status_code=401)
        alive = session.process is not None and session.process.poll() is None
        return JSONResponse({"ready": alive}, status_code=200 if alive else 410)

    @app.websocket("/terminal")
    async def terminal(socket: WebSocket):
        if not authorized(socket.headers):
            await socket.close(code=1008)
            return
        await socket.accept()
        if session.queue is not None:
            await socket.close(code=4009, reason="该终端已在其他页面连接")
            return
        queue: asyncio.Queue = asyncio.Queue(maxsize=128)
        session.queue = queue
        try:
            await socket.send_bytes(b"\x1bc" + b"".join(session.output))
            async def output():
                while not session.finished.is_set():
                    if session.queue is not queue:
                        await socket.close(code=1013, reason="终端输出过快，请重新连接")
                        return
                    try:
                        data = await asyncio.wait_for(queue.get(), timeout=0.5)
                    except TimeoutError:
                        continue
                    await socket.send_bytes(data)
                while not queue.empty():
                    await socket.send_bytes(queue.get_nowait())
                await socket.send_text(json.dumps({"type": "exit", "code": session.process.poll()}))
                await socket.close(code=4000, reason="Agent 已退出")
            async def input_():
                while True:
                    message = await socket.receive_json()
                    if not isinstance(message, dict):
                        raise ValueError("终端消息必须为对象")
                    kind = message.get("type")
                    if kind == "input":
                        text = message.get("data")
                        if not isinstance(text, str) or len(text.encode()) > MAX_INPUT_BYTES:
                            raise ValueError("终端输入过大或格式错误")
                        await session.write_input(text.encode())
                    elif kind == "resize":
                        cols, rows = message.get("cols"), message.get("rows")
                        if type(cols) is not int or type(rows) is not int:
                            raise ValueError("终端尺寸必须为整数")
                        session.resize(cols, rows)
                    elif kind != "ping":
                        raise ValueError("未知终端消息类型")
            tasks = [asyncio.create_task(output()), asyncio.create_task(input_())]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            # ASGI 连接取消只释放订阅，PTY 由断线宽限或 runtime 回收。
            logger.info("终端订阅已取消 pid=%s", session.process.pid)
        except (WebSocketDisconnect, OSError, RuntimeError):
            logger.info("终端连接已断开 pid=%s", session.process.pid)
        except (ValueError, TypeError):
            await socket.close(code=1008, reason="终端消息格式错误")
        finally:
            if session.queue is queue:
                session.queue = None
                session.disconnected_at = time.monotonic()

    return app


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        # 不使用 preexec_fn：在独立解释器内建立控制终端，再原位执行 Agent。
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
        os.execvpe(sys.argv[2], sys.argv[2:], os.environ)
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("必须指定 Agent 命令")
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    app = create_terminal_app(command, os.environ.pop(AUTH_ENV))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, access_log=False,
                                        timeout_graceful_shutdown=2, ws_max_size=128 * 1024))
    app.state.server = server
    server.run()


if __name__ == "__main__":
    main()
