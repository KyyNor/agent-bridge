from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from agent_bridge.agent_runtime.adapters.opencode_server import OpenCodeServerProcess


class _AsyncPipe:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0)


class _FakeProcess:
    def __init__(self) -> None:
        self.stdout = _AsyncPipe([b"opencode server listening on http://127.0.0.1:43214\n"])
        self.stderr = _AsyncPipe([])
        self.returncode = None
        self.terminate_count = 0
        self.kill_count = 0

    def terminate(self) -> None:
        self.terminate_count += 1
        self.returncode = 0

    def kill(self) -> None:
        self.kill_count += 1
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode or 0


class _Response:
    status_code = 200
    is_error = False
    text = ""

    def json(self):
        return {"id": "ses_123"}


class _SseResponse:
    is_error = False
    status_code = 200
    text = ""

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines

    async def aread(self) -> bytes:
        return b""

    async def aiter_lines(self):
        for line in self.lines:
            yield line


class _StreamContext:
    def __init__(self, response: _SseResponse) -> None:
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


class _FakeClient:
    instances: list["_FakeClient"] = []

    def __init__(self, *args, **kwargs) -> None:
        self.base_url = kwargs.get("base_url")
        self.requests: list[tuple[str, str, dict[str, str] | None, dict | None]] = []
        self.stream_requests: list[tuple[str, str, dict[str, str] | None]] = []
        self.sse_lines: list[str] = []
        self.closed = False
        self.__class__.instances.append(self)

    async def get(self, path: str):
        self.requests.append(("GET", path, None, None))
        return _Response()

    async def request(self, method: str, path: str, *, params=None, json=None):
        self.requests.append((method, path, params, json))
        return _Response()

    def stream(self, method: str, path: str, *, params=None, headers=None):
        self.stream_requests.append((method, path, params))
        return _StreamContext(_SseResponse(self.sse_lines))

    async def aclose(self) -> None:
        self.closed = True


def test_opencode_server_starts_on_reported_port_and_is_reaped(monkeypatch, tmp_path: Path) -> None:
    process = _FakeProcess()
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    async def run() -> None:
        server = OpenCodeServerProcess(command="opencode")
        await server.start(cwd=tmp_path)
        response = await server.request_json(
            "POST",
            "/session",
            params={"directory": str(tmp_path)},
            payload={},
        )
        assert response == {"id": "ses_123"}
        assert captured["args"] == (
            "opencode",
            "serve",
            "--port",
            "0",
            "--hostname",
            "127.0.0.1",
        )
        assert captured["kwargs"]["cwd"] == str(tmp_path)
        client = _FakeClient.instances[-1]
        assert client.base_url == "http://127.0.0.1:43214"
        assert client.requests[-1][0:3] == (
            "POST",
            "/session",
            {"directory": str(tmp_path)},
        )
        await server.close()

    asyncio.run(run())
    assert process.terminate_count == 1
    assert process.kill_count == 0
    assert _FakeClient.instances[-1].closed is True


def test_opencode_server_decodes_json_sse_frames(monkeypatch, tmp_path: Path) -> None:
    process = _FakeProcess()

    async def fake_create_subprocess_exec(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    async def run() -> None:
        server = OpenCodeServerProcess(command="opencode")
        await server.start(cwd=tmp_path)
        client = _FakeClient.instances[-1]
        client.sse_lines = [
            ": keep-alive",
            "event: message",
            'data: {"type":"server.connected","properties":{}}',
            "",
            'data: {"type":"session.idle",',
            'data: "properties":{"sessionID":"ses_123"}}',
            "",
        ]
        events = [
            event
            async for event in server.stream_json_events(
                "/event", params={"directory": str(tmp_path)}
            )
        ]
        assert events == [
            {"type": "server.connected", "properties": {}},
            {"type": "session.idle", "properties": {"sessionID": "ses_123"}},
        ]
        assert client.stream_requests == [
            ("GET", "/event", {"directory": str(tmp_path)})
        ]
        await server.close()

    asyncio.run(run())
