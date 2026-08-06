"""模型评估的领域类型与数据集注册表。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


EvaluationDimension = Literal[
    "general_knowledge", "math", "instruction_following", "code", "agent"
]
RunnerKey = Literal["opencompass", "code", "swebench"]


@dataclass(frozen=True)
class DatasetSpec:
    """一个可在 Agent Bridge 中选择的评估数据集。"""

    key: str
    label: str
    description: str
    dimension: EvaluationDimension
    runner_key: RunnerKey
    metric: str
    image_role: str
    default_max_samples: int = 64


DIMENSION_LABELS: dict[EvaluationDimension, str] = {
    "general_knowledge": "通用知识",
    "math": "数学",
    "instruction_following": "指令遵循",
    "code": "代码",
    "agent": "Agent",
}


DATASET_SPECS: tuple[DatasetSpec, ...] = (
    DatasetSpec(
        key="ceval_gen",
        label="C-Eval",
        description="中文多学科知识与理解。",
        dimension="general_knowledge",
        runner_key="opencompass",
        metric="accuracy",
        image_role="opencompass",
    ),
    DatasetSpec(
        key="mmlu_pro_gen",
        label="MMLU-Pro",
        description="更严格的英文多学科知识与推理。",
        dimension="general_knowledge",
        runner_key="opencompass",
        metric="accuracy",
        image_role="opencompass",
    ),
    DatasetSpec(
        key="gsm8k_chat_gen",
        label="GSM8K",
        description="基础数学文字题推理。",
        dimension="math",
        runner_key="opencompass",
        metric="accuracy",
        image_role="opencompass",
    ),
    DatasetSpec(
        key="ifeval_gen",
        label="IFEval",
        description="格式、长度和内容约束等指令遵循。",
        dimension="instruction_following",
        runner_key="opencompass",
        metric="prompt_level_strict_acc",
        image_role="opencompass",
    ),
    DatasetSpec(
        key="humaneval",
        label="HumanEval",
        description="Python 函数生成与隐藏测试，通过率按 pass@1 统计。",
        dimension="code",
        runner_key="code",
        metric="pass@1",
        image_role="agent_worker",
    ),
    DatasetSpec(
        key="mbpp",
        label="MBPP",
        description="基础 Python 编程问题与执行测试，通过率按 pass@1 统计。",
        dimension="code",
        runner_key="code",
        metric="pass@1",
        image_role="agent_worker",
    ),
    DatasetSpec(
        key="swebench_lite",
        label="SWE-bench Lite",
        description="真实仓库 Issue 修复；以通过隐藏测试的 resolved rate 统计。",
        dimension="agent",
        runner_key="swebench",
        metric="resolved_rate",
        image_role="agent_worker",
        default_max_samples=5,
    ),
)

DATASETS_BY_KEY = {spec.key: spec for spec in DATASET_SPECS}
