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


class _FakeClient:
    instances: list["_FakeClient"] = []

    def __init__(self, *args, **kwargs) -> None:
        self.base_url = kwargs.get("base_url")
        self.requests: list[tuple[str, str, dict[str, str] | None, dict | None]] = []
        self.closed = False
        self.__class__.instances.append(self)

    async def get(self, path: str):
        self.requests.append(("GET", path, None, None))
        return _Response()

    async def request(self, method: str, path: str, *, params=None, json=None):
        self.requests.append((method, path, params, json))
        return _Response()

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
