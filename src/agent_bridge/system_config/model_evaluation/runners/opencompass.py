"""普通 OpenCompass 数据集的 Docker runner。"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from agent_bridge.system_config.model_evaluation.runtimes import ContainerRuntime, ContainerSpec

from .protocol import ContainerReporter, ExecutionRequest, ProgressReporter


_CONFIG_IMPORTS = {
    "ceval_gen": ("from opencompass.configs.datasets.ceval.ceval_gen import ceval_datasets", "ceval_datasets"),
    "mmlu_pro_gen": ("from opencompass.configs.datasets.mmlu_pro.mmlu_pro_gen import mmlu_pro_datasets", "mmlu_pro_datasets"),
    "gsm8k_chat_gen": ("from opencompass.configs.datasets.gsm8k.gsm8k_gen import gsm8k_datasets", "gsm8k_datasets"),
    "ifeval_gen": ("from opencompass.configs.datasets.IFEval.IFEval_gen import ifeval_datasets", "ifeval_datasets"),
}


class OpenCompassRunner:
    key = "opencompass"

    def execute(self, request: ExecutionRequest, runtime: ContainerRuntime, *, report_container: ContainerReporter, report_progress: ProgressReporter) -> dict[str, Any]:
        report_progress("正在准备 OpenCompass 配置")
        config_path = request.work_dir / "evaluation.py"
        config_path.write_text(
            self.render_config(request.model_name, request.datasets, request.max_samples, request.sampling_mode, request.sample_seed),
            encoding="utf-8",
        )
        spec = ContainerSpec(
            image=request.opencompass_image,
            command=("opencompass", "/workspace/evaluation.py", "-w", "/workspace/output"),
            work_dir=request.work_dir,
            labels=_labels(request),
            environment={"OPENAI_API_KEY": request.api_key, "OPENAI_BASE_URL": request.base_url},
        )
        report_progress("OpenCompass 正在执行推理与评分")
        handle = runtime.run(spec, log_path=request.work_dir / "runner.log")
        report_container(handle)
        return_code = runtime.wait(handle)
        if return_code:
            raise RuntimeError(f"OpenCompass 容器退出，退出码 {return_code}；请查看评估日志")
        return collect_opencompass_results(request.work_dir / "output")

    @staticmethod
    def render_config(model_name: str, datasets: tuple[str, ...], max_samples: int, sampling_mode: str, sample_seed: int) -> str:
        imports: list[str] = []
        dataset_names: list[tuple[str, str]] = []
        for key in datasets:
            line, variable = _CONFIG_IMPORTS[key]
            imports.append(line)
            dataset_names.append((key, variable))
        model_literal = json.dumps(model_name, ensure_ascii=False)
        return "\n".join([
            "import json",
            "import random",
            "from pathlib import Path",
            "from mmengine.config import read_base",
            "from opencompass.models import OpenAI",
            "from opencompass.openicl.icl_dataset_reader import DatasetReader",
            "from opencompass.registry import ICL_DATASET_READERS",
            "import opencompass.datasets.base as dataset_base",
            "",
            "@ICL_DATASET_READERS.register_module(force=True)",
            "class AgentBridgeSampleDatasetReader(DatasetReader):",
            "    def __init__(self, *args, sample_count=64, sample_mode='head', sample_seed=42, sample_dataset_key='', sample_manifest_path='', **kwargs):",
            "        kwargs.pop('test_range', None)",
            "        super().__init__(*args, test_range=None, **kwargs)",
            "        source_indices = list(range(len(self.dataset['test'])))",
            "        if sample_mode == 'random':",
            "            random.Random(f'{sample_seed}:{sample_dataset_key}').shuffle(source_indices)",
            "        selected_indices = source_indices[:sample_count]",
            "        source_ids = [self.dataset['test'][index].get('idx', index) for index in selected_indices]",
            "        self.dataset['test'] = self.dataset['test'].select(selected_indices)",
            "        if sample_manifest_path:",
            "            Path(sample_manifest_path).write_text(json.dumps(dict(dataset=sample_dataset_key, mode=sample_mode, seed=sample_seed, source_indices=selected_indices, source_ids=source_ids), ensure_ascii=False), encoding='utf-8')",
            "",
            "dataset_base.DatasetReader = AgentBridgeSampleDatasetReader",
            "",
            "with read_base():",
            *[f"    {line}" for line in imports],
            "",
            f"dataset_groups = {[(key, variable) for key, variable in dataset_names]!r}",
            "datasets = []",
            "for group_key, group_variable in dataset_groups:",
            "    group_datasets = globals()[group_variable]",
            "    allocation_order = list(range(len(group_datasets)))",
            "    if " + repr(sampling_mode) + " == 'random':",
            f"        random.Random(f'{sample_seed}:{{group_key}}:allocation').shuffle(allocation_order)",
            f"    base_count, remainder = divmod({max_samples}, len(group_datasets))",
            "    allocation = [base_count] * len(group_datasets)",
            "    for allocation_index in allocation_order[:remainder]:",
            "        allocation[allocation_index] += 1",
            "    for dataset_index, dataset in enumerate(group_datasets):",
            "        sample_count = allocation[dataset_index]",
            "        if not sample_count:",
            "            continue",
            "        dataset['reader_cfg'].pop('test_range', None)",
            "        dataset['reader_cfg'].update(dict(",
            f"            sample_count=sample_count, sample_mode={json.dumps(sampling_mode)}, sample_seed={sample_seed},",
            "            sample_dataset_key=f'{group_key}:{dataset[\"abbr\"]}',",
            "            sample_manifest_path=str(Path.cwd() / f\"sample-manifest-{dataset['abbr']}.json\"),",
            "        ))",
            "        datasets.append(dataset)",
            "api_meta_template = dict(round=[dict(role='HUMAN', api_role='HUMAN'), dict(role='BOT', api_role='BOT', generate=True)])",
            "models = [dict(",
            f"    abbr={model_literal}, type=OpenAI, path={model_literal}, key='ENV',",
            "    openai_api_base=__import__('os').environ['OPENAI_BASE_URL'].rstrip('/') + '/chat/completions',",
            "    meta_template=api_meta_template, query_per_second=1, max_out_len=1024, max_seq_len=4096, batch_size=1,",
            ")]",
            "",
        ])


def _labels(request: ExecutionRequest) -> dict[str, str]:
    return {
        "com.agent-bridge.managed": "true",
        "com.agent-bridge.evaluation.run_id": request.run_id,
        "com.agent-bridge.evaluation.execution_id": request.execution_id,
        "com.agent-bridge.evaluation.runner": "opencompass",
    }


def collect_opencompass_results(output_dir: Path) -> dict[str, Any]:
    rows: list[dict[str, str]] = []
    seen: set[Path] = set()
    for pattern in ("summary/*.csv", "**/summary*.csv"):
        for csv_path in output_dir.glob(pattern):
            if csv_path in seen:
                continue
            seen.add(csv_path)
            with csv_path.open(encoding="utf-8-sig", newline="") as handle:
                rows.extend({key: value or "" for key, value in row.items()} for row in csv.DictReader(handle))
    manifests: list[dict[str, Any]] = []
    for manifest_path in output_dir.parent.glob("sample-manifest-*.json"):
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            manifests.append(payload)
    return {"rows": rows, "summary_found": bool(rows), "sample_manifests": manifests}
