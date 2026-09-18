"""DSH Web 替身服务器：真实子进程 smoke test 使用。

以 ``python dsh_test_workspace_server.py <port>`` 启动，模拟真实 DSH 的关键行为：

- 启动时打印 ``dsh web: http://127.0.0.1:<port>/?token=<token>`` 横幅；
- 首次导航鉴权：无会话 Cookie 且无 token → 401（与 DSH 一致）；带正确 token
  的 ``GET /`` → 303 回 ``/`` 并写入会话 Cookie；
- 其他 HTTP 路径：``/redirect``（302 → /login，验证 Location 改写）、
  ``/set-cookie``（Path=/，验证 Cookie Path 改写）；
- 同一端口提供 WebSocket echo。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import sys
from urllib.parse import parse_qs, urlsplit

from websockets.asyncio.server import serve

TOKEN = "standin-launch-token"
UNAUTHORIZED_BODY = "dsh web authentication required; reopen the URL printed by dsh web.\n"


def session_cookie_name(authority: str) -> str:
    """与真实 DSH 相同的会话 Cookie 名：authority 绑定的 dsh-auth-<hash>。"""
    digest = hashlib.sha256(authority.encode("utf-8")).digest()
    return "dsh-auth-" + base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def parse_args(argv: list[str]) -> tuple[int, str | None]:
    """解析与 DSH 相同的启动参数：``--patch <file> --host <h> --port <n>``。"""
    port: int | None = None
    patch: str | None = None
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--port" and index + 1 < len(argv):
            port = int(argv[index + 1])
            index += 2
        elif token == "--patch" and index + 1 < len(argv):
            patch = argv[index + 1]
            index += 2
        elif token == "--host" and index + 1 < len(argv):
            index += 2
        else:
            index += 1
    if port is None:
        raise SystemExit("standin: --port is required")
    return port, patch


def _cookie_value(header: str | None, name: str) -> str | None:
    if not header:
        return None
    for segment in header.split(";"):
        if "=" not in segment:
            continue
        cookie_name, _, value = segment.partition("=")
        if cookie_name.strip() == name:
            return value.strip()
    return None


async def _handler(connection) -> None:
    async for message in connection:
        await connection.send(message)


async def _process_request(connection, request, patch_path: str | None):
    if request.headers.get("Upgrade", "").lower() == "websocket":
        return None
    parts = urlsplit(request.path)
    path = parts.path
    query = parse_qs(parts.query)
    authority = request.headers.get("Host") or ""
    cookie_name = session_cookie_name(authority)
    authenticated = _cookie_value(request.headers.get("Cookie"), cookie_name) == TOKEN

    if path == "/set-cookie":
        response = connection.respond(200, "ok")
        response.headers["Set-Cookie"] = f"{cookie_name}={TOKEN}; Path=/; HttpOnly"
        return response
    if path == "/redirect":
        response = connection.respond(302, "")
        response.headers["Location"] = "/login"
        return response
    if path == "/" and query.get("token", [None])[0] == TOKEN:
        response = connection.respond(303, "")
        response.headers["Location"] = "/"
        response.headers["Set-Cookie"] = f"{cookie_name}={TOKEN}; Path=/; HttpOnly; SameSite=Strict"
        return response
    if not authenticated and not path.startswith("/login"):
        return connection.respond(401, UNAUTHORIZED_BODY)
    if path == "/patch":
        return connection.respond(200, patch_path or "none")
    return connection.respond(200, "dsh-standin")


async def main() -> None:
    port, patch_path = parse_args(sys.argv[1:])
    async def process_request(connection, request):
        return await _process_request(connection, request, patch_path)

    async with serve(_handler, "127.0.0.1", port, process_request=process_request):
        print(f"dsh web: http://127.0.0.1:{port}/?token={TOKEN}", flush=True)
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
