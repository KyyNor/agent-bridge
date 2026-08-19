"""评估 runner 的统一协议。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from agent_bridge.system_config.model_evaluation.runtimes import ContainerHandle, ContainerRuntime


@dataclass(frozen=True)
class ExecutionRequest:
    run_id: str
    execution_id: str
    work_dir: Path
    model_name: str
    base_url: str
    api_key: str
    datasets: tuple[str, ...]
    max_samples: int
    sampling_mode: str
    sample_seed: int
    opencompass_image: str
    agent_worker_image: str
    swebench_manifest_path: Path


ContainerReporter = Callable[[ContainerHandle], None]
ProgressReporter = Callable[[str], None]

LOG_TAIL_MAX_CHARS = 4000


def container_log_tail(log_path: Path) -> str:
    """读取容器日志尾部，容器失败时随错误信息返回，便于直接定位容器内报错。"""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"无法读取日志 {log_path}：{exc}"
    tail = text[-LOG_TAIL_MAX_CHARS:].strip()
    if not tail:
        return f"日志为空：{log_path}"
    return f"日志 {log_path} 尾部内容：\n{tail}"


class EvaluationRunner(Protocol):
    key: str

    def execute(
        self,
        request: ExecutionRequest,
        runtime: ContainerRuntime,
        *,
        report_container: ContainerReporter,
        report_progress: ProgressReporter,
    ) -> dict[str, Any]: ...
