"""agent-bridge-code-generate 的超时传参与单题失败隔离回归测试。"""

from __future__ import annotations

import json
import runpy
from pathlib import Path
from typing import Any

import pytest


_GENERATE_PATH = (
    Path(__file__).resolve().parents[1]
    / "docker/model-evaluation/opencompass/scripts/agent-bridge-code-generate"
)
_GENERATE: dict[str, Any] = runpy.run_path(str(_GENERATE_PATH))


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None


def test_model_completion_passes_timeout_to_urlopen(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_urlopen(request: Any, timeout: float | None = None) -> _FakeResponse:
        captured["timeout"] = timeout
        return _FakeResponse(json.dumps({"choices": [{"message": {"content": "print(1)"}}]}).encode())

    monkeypatch.setattr(_GENERATE["urllib"].request, "urlopen", fake_urlopen)

    completion = _GENERATE["model_completion"]("prompt", "model", "http://api.example/v1", "key")

    assert completion == "print(1)"
    assert captured["timeout"] == _GENERATE["MODEL_TIMEOUT_SECONDS"] == 300


def test_single_case_failure_is_recorded_and_batch_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    (data_root / "humaneval.jsonl").write_text(
        json.dumps({"task_id": "HumanEval/0", "prompt": "def double(value):\n", "test": "def check(c):\n    assert c(3) == 6\n", "entry_point": "double"}),
        encoding="utf-8",
    )
    (data_root / "mbpp.jsonl").write_text(
        json.dumps({"task_id": "mbpp/1", "text": "返回两倍", "test_list": ["assert double(3) == 6"]}),
        encoding="utf-8",
    )
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps({"model_name": "m", "datasets": ["humaneval", "mbpp"], "max_samples": 1, "sampling_mode": "head", "sample_seed": 42}),
        encoding="utf-8",
    )
    output_path = tmp_path / "generated-cases.json"
    monkeypatch.setenv("AGENT_BRIDGE_CODE_DATA_ROOT", str(data_root))
    monkeypatch.setenv("OPENAI_BASE_URL", "http://api.example/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "key")

    calls: list[str] = []

    def fake_model_completion(prompt: str, model: str, base_url: str, api_key: str) -> str:
        calls.append(prompt)
        if "def double" in prompt:
            raise TimeoutError("timed out")
        return "def double(value):\n    return value * 2"

    monkeypatch.setitem(_GENERATE, "model_completion", fake_model_completion)

    # runpy.run_path 返回的命名空间是副本，需要 patch main 的真实 __globals__ 才能生效。
    main = _GENERATE["main"]
    monkeypatch.setitem(main.__globals__, "model_completion", fake_model_completion)
    monkeypatch.setattr("sys.argv", ["agent-bridge-code-generate", str(request_path), str(output_path)])
    exit_code = main()

    assert exit_code == 0
    assert len(calls) == 2
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    cases = {case["case_id"]: case for case in payload["cases"]}
    failed = cases["HumanEval/0"]
    assert failed["completion"] == ""
    assert failed["generation_error"] == "TimeoutError: timed out"
    passed = cases["mbpp/1"]
    assert "return value * 2" in passed["completion"]
    assert passed["generation_error"] == ""
