"""code-sandbox 对 HumanEval completion 的缩进回归测试。"""

from __future__ import annotations

import json
import runpy
from pathlib import Path
from typing import Any

import pytest


_SANDBOX_PATH = (
    Path(__file__).resolve().parents[1]
    / "docker/model-evaluation/agent-worker/scripts/code-sandbox"
)
_SANDBOX: dict[str, Any] = runpy.run_path(str(_SANDBOX_PATH))


def _human_eval_program(completion: str) -> str:
    program_for = _SANDBOX["program_for"]
    return program_for({
        "dataset": "humaneval",
        "completion": completion,
        "prompt": "def double(value):\n",
        "test": "def check(candidate):\n    assert candidate(3) == 6\n",
        "entry_point": "double",
    })


def test_humaneval_body_completion_preserves_leading_indentation() -> None:
    source = _human_eval_program("    return value * 2\n")

    assert "\n    return value * 2\n" in source
    exec(source, {})


def test_humaneval_fenced_body_completion_preserves_leading_indentation() -> None:
    source = _human_eval_program("```python\n    return value * 2\n```")

    assert "\n    return value * 2\n" in source
    exec(source, {})


def test_generation_failure_case_is_marked_failed_and_error_passed_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case_path = tmp_path / "case.json"
    case_path.write_text(
        json.dumps({
            "dataset": "humaneval",
            "source_index": 0,
            "case_id": "HumanEval/0",
            "prompt": "def double(value):\n",
            "completion": "",
            "generation_error": "TimeoutError: timed out",
            "test": "def check(c):\n    assert c(3) == 6\n",
            "entry_point": "double",
        }),
        encoding="utf-8",
    )
    result_path = tmp_path / "result.json"

    monkeypatch.setattr("sys.argv", ["code-sandbox", str(case_path), str(result_path)])
    main = _SANDBOX["main"]
    exit_code = main()

    assert exit_code == 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["passed"] is False
    assert result["generation_error"] == "TimeoutError: timed out"
