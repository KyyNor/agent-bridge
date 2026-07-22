from __future__ import annotations

import asyncio
from pathlib import Path

from agent_bridge.agent_runtime.adapters.jsonl_cli import (
    JsonlCliProcess,
    decode_json_line,
    effective_prompt,
    extract_json_object,
)
from agent_bridge.agent_runtime.types import CodingAgentRequest


class _AsyncPipe:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0)


def _request(tmp_path: Path, **kwargs) -> CodingAgentRequest:
    return CodingAgentRequest(
        prompt="用户提示词",
        cwd=tmp_path,
        mcp_servers={},
        setting_sources=[],
        **kwargs,
    )


def test_jsonl_helpers_keep_cli_fallback_contract(tmp_path: Path) -> None:
    request = _request(tmp_path, system_prompt_append="系统补充")

    assert effective_prompt(request) == "系统补充\n\n用户提示词"
    assert decode_json_line('{"type":"result","value":1}') == {
        "type": "result",
        "value": 1,
    }
    assert decode_json_line("plain output") == {"type": "stdout", "message": "plain output"}
    assert decode_json_line("[1, 2]") == {"type": "stdout", "value": [1, 2]}
    assert extract_json_object('before {"answer": 42} after') == {"answer": 42}


def test_jsonl_process_collects_rows_stderr_and_native_messages(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    native_messages: list[object] = []
    stderr_messages: list[str] = []

    class _FakeProcess:
        stdout = _AsyncPipe([b'\n', b'{"type":"result","result":"ok"}\n', b'plain\n'])
        stderr = _AsyncPipe([b'warning one\n', b'warning two\n'])
        returncode = 0

        async def wait(self) -> int:
            return 0

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    request = _request(
        tmp_path,
        on_native_message=native_messages.append,
        stderr=stderr_messages.append,
    )
    cli = JsonlCliProcess(
        request=request,
        args=["example", "--json"],
        cwd=str(tmp_path),
        forward_native_messages=True,
    )

    async def run_process() -> tuple[list[dict[str, object]], int]:
        await cli.start()
        rows = [row async for row in cli.rows()]
        return rows, await cli.wait()

    rows, return_code = asyncio.run(run_process())

    assert captured["args"] == ("example", "--json")
    assert captured["kwargs"]["cwd"] == str(tmp_path)
    assert rows == [
        {"type": "result", "result": "ok"},
        {"type": "stdout", "message": "plain"},
    ]
    assert native_messages == rows
    assert stderr_messages == ["warning one\n", "warning two\n"]
    assert cli.stderr_summary() == "warning one\nwarning two"
    assert return_code == 0


def test_jsonl_process_abort_escalates_from_terminate_to_kill(tmp_path: Path) -> None:
    class _HangingProcess:
        stdout = _AsyncPipe([])
        stderr = _AsyncPipe([])
        returncode = None

        def __init__(self) -> None:
            self.terminate_count = 0
            self.kill_count = 0

        def terminate(self) -> None:
            self.terminate_count += 1

        def kill(self) -> None:
            self.kill_count += 1
            self.returncode = -9

        async def wait(self) -> int:
            if self.returncode is not None:
                return self.returncode
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    process = _HangingProcess()
    cli = JsonlCliProcess(request=_request(tmp_path), args=["example"])
    cli._process = process  # type: ignore[assignment]

    asyncio.run(cli.abort(timeout_seconds=0))

    assert process.terminate_count == 1
    assert process.kill_count == 1
    assert process.returncode == -9
