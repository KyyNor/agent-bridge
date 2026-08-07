"""code-sandbox 对 HumanEval completion 的缩进回归测试。"""

from __future__ import annotations

import runpy
from pathlib import Path
from typing import Any


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
