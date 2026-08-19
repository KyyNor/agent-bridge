"""HumanEval 与 MBPP 的双容器评估 runner。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_bridge.system_config.model_evaluation.runtimes import ContainerRuntime, ContainerSpec

from .protocol import ContainerReporter, ExecutionRequest, ProgressReporter, container_log_tail


class CodeRunner:
    key = "code"

    def execute(self, request: ExecutionRequest, runtime: ContainerRuntime, *, report_container: ContainerReporter, report_progress: ProgressReporter) -> dict[str, Any]:
        request.work_dir.mkdir(parents=True, exist_ok=True)
        request_path = request.work_dir / "request.json"
        request_path.write_text(json.dumps(_request_payload(request), ensure_ascii=False), encoding="utf-8")
        generator = ContainerSpec(
            image=request.opencompass_image,
            command=("agent-bridge-code-generate", "/workspace/request.json", "/workspace/generated-cases.json"),
            work_dir=request.work_dir,
            labels=_labels(request, "code-generation"),
            environment={"OPENAI_API_KEY": request.api_key, "OPENAI_BASE_URL": request.base_url},
        )
        report_progress("正在生成 HumanEval / MBPP 代码")
        generation_handle = runtime.run(generator, log_path=request.work_dir / "generation.log")
        report_container(generation_handle)
        if runtime.wait(generation_handle):
            raise RuntimeError(f"代码生成容器执行失败；{container_log_tail(request.work_dir / 'generation.log')}")
        generated_path = request.work_dir / "generated-cases.json"
        payload = json.loads(generated_path.read_text(encoding="utf-8"))
        cases = payload.get("cases") if isinstance(payload, dict) else None
        if not isinstance(cases, list):
            raise RuntimeError("代码生成容器未返回 cases 列表")
        completed: list[dict[str, Any]] = []
        for index, case in enumerate(cases, start=1):
            case_dir = request.work_dir / "cases" / str(index)
            case_dir.mkdir(parents=True, exist_ok=True)
            (case_dir / "case.json").write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
            sandbox = ContainerSpec(
                image=request.agent_worker_image,
                command=("code-sandbox", "/workspace/case.json", "/workspace/result.json"),
                work_dir=case_dir,
                labels=_labels(request, "code-sandbox"),
                network="none",
                read_only=True,
                cap_drop_all=True,
                no_new_privileges=True,
                pids_limit=64,
                memory="512m",
                cpus=1.0,
            )
            report_progress(f"正在执行代码测试（{index}/{len(cases)}）")
            handle = runtime.run(sandbox, log_path=case_dir / "sandbox.log")
            report_container(handle)
            return_code = runtime.wait(handle)
            result_path = case_dir / "result.json"
            result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
            completed.append({"return_code": return_code, **result})
        return _summarize(completed, request)


def _request_payload(request: ExecutionRequest) -> dict[str, Any]:
    return {
        "model_name": request.model_name,
        "base_url": request.base_url,
        "datasets": list(request.datasets),
        "max_samples": request.max_samples,
        "sampling_mode": request.sampling_mode,
        "sample_seed": request.sample_seed,
    }


def _labels(request: ExecutionRequest, stage: str) -> dict[str, str]:
    return {
        "com.agent-bridge.managed": "true",
        "com.agent-bridge.evaluation.run_id": request.run_id,
        "com.agent-bridge.evaluation.execution_id": request.execution_id,
        "com.agent-bridge.evaluation.runner": "code",
        "com.agent-bridge.evaluation.stage": stage,
    }


def _summarize(cases: list[dict[str, Any]], request: ExecutionRequest) -> dict[str, Any]:
    rows: list[dict[str, str]] = []
    manifests: list[dict[str, Any]] = []
    for dataset in request.datasets:
        selected = [case for case in cases if case.get("dataset") == dataset]
        passed = sum(1 for case in selected if case.get("passed") is True)
        total = len(selected)
        rows.append({"dataset": dataset, "metric": "pass@1", "score": f"{(passed / total * 100) if total else 0:.2f}"})
        manifests.append({
            "dataset": dataset,
            "mode": request.sampling_mode,
            "seed": request.sample_seed,
            "source_indices": [case.get("source_index") for case in selected],
            "source_ids": [case.get("case_id") for case in selected],
        })
    return {"rows": rows, "summary_found": True, "sample_manifests": manifests, "cases": cases}
