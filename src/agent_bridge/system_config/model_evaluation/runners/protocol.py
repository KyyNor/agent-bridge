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


ContainerReporter = Callable[[ContainerHandle], None]
ProgressReporter = Callable[[str], None]


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
