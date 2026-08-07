"""SWE-bench Agent runner 与主服务 testbed 工具桥。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from agent_bridge.system_config.model_evaluation.runtimes import ContainerHandle, ContainerRuntime, ContainerSpec

from .protocol import ContainerReporter, ExecutionRequest, ProgressReporter


class SWEbenchRunner:
    key = "swebench"

    def execute(self, request: ExecutionRequest, runtime: ContainerRuntime, *, report_container: ContainerReporter, report_progress: ProgressReporter) -> dict[str, Any]:
        request_path = request.work_dir / "request.json"
        request_path.write_text(json.dumps({
            "model_name": request.model_name,
            "base_url": request.base_url,
            "datasets": list(request.datasets),
            "max_tasks": request.max_samples,
            "sampling_mode": request.sampling_mode,
            "sample_seed": request.sample_seed,
            "tool_request_path": "/workspace/tool-requests.jsonl",
            "tool_result_path": "/workspace/tool-results.jsonl",
        }, ensure_ascii=False), encoding="utf-8")
        spec = ContainerSpec(
            image=request.agent_worker_image,
            command=("swe-agent", "/workspace/request.json", "/workspace/result.json"),
            work_dir=request.work_dir,
            labels=_labels(request, "swe-agent"),
            environment={"OPENAI_API_KEY": request.api_key, "OPENAI_BASE_URL": request.base_url},
        )
        report_progress("SWE-bench Agent 正在执行；将按任务启动隔离 testbed")
        agent_handle = runtime.run(spec, log_path=request.work_dir / "runner.log")
        report_container(agent_handle)
        testbeds: dict[str, ContainerHandle] = {}
        seen_request_ids: set[str] = set()
        requests_path = request.work_dir / "tool-requests.jsonl"
        results_path = request.work_dir / "tool-results.jsonl"
        try:
            while runtime.poll(agent_handle) is None:
                for tool_request in _read_new_requests(requests_path, seen_request_ids):
                    response = self._handle_tool_request(
                        tool_request,
                        request=request,
                        runtime=runtime,
                        testbeds=testbeds,
                        report_container=report_container,
                        report_progress=report_progress,
                    )
                    _append_jsonl(results_path, response)
                time.sleep(0.1)
            return_code = runtime.wait(agent_handle)
        finally:
            for handle in testbeds.values():
                runtime.stop(handle)
        if return_code:
            raise RuntimeError("SWE-bench Agent 容器执行失败；请查看 runner.log")
        result_path = request.work_dir / "result.json"
        if not result_path.exists():
            raise RuntimeError("SWE-bench Agent 未产出 result.json")
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("SWE-bench Agent 结果格式错误")
        if payload.get("error"):
            raise RuntimeError(str(payload["error"]))
        return payload

    def _handle_tool_request(
        self,
        tool_request: dict[str, Any],
        *,
        request: ExecutionRequest,
        runtime: ContainerRuntime,
        testbeds: dict[str, ContainerHandle],
        report_container: ContainerReporter,
        report_progress: ProgressReporter,
    ) -> dict[str, Any]:
        request_id = str(tool_request.get("request_id") or "")
        action = str(tool_request.get("type") or "")
        if not request_id:
            return {"request_id": request_id, "ok": False, "error": "工具请求缺少 request_id"}
        if action == "start_task":
            task = tool_request.get("task")
            if not isinstance(task, dict):
                return {"request_id": request_id, "ok": False, "error": "start_task 缺少 task"}
            instance_id = str(task.get("instance_id") or "")
            image = str(task.get("testbed_image") or "")
            if not instance_id or not image or not runtime.image_exists(image):
                return {"request_id": request_id, "ok": False, "error": f"SWE testbed 镜像未就绪：{image or instance_id}"}
            testbed_dir = request.work_dir / "testbeds" / instance_id.replace("/", "_")
            testbed = ContainerSpec(
                image=image,
                command=("sh", "-c", "while true; do sleep 3600; done"),
                work_dir=testbed_dir,
                labels=_labels(request, "swe-testbed") | {"com.agent-bridge.evaluation.instance_id": instance_id},
                network="none",
                cap_drop_all=True,
                no_new_privileges=True,
                pids_limit=256,
                memory="4g",
                cpus=2.0,
                mount_workspace=False,
                container_workdir=str(task.get("workdir") or "/testbed"),
            )
            report_progress(f"正在启动 SWE testbed：{instance_id}")
            handle = runtime.run(testbed, log_path=testbed_dir / "testbed.log")
            testbeds[instance_id] = handle
            report_container(handle)
            return {"request_id": request_id, "ok": True, "instance_id": instance_id}
        instance_id = str(tool_request.get("instance_id") or "")
        handle = testbeds.get(instance_id)
        if handle is None:
            return {"request_id": request_id, "ok": False, "error": f"未知 SWE testbed：{instance_id}"}
        if action == "command":
            command = str(tool_request.get("command") or "")
            if not command:
                return {"request_id": request_id, "ok": False, "error": "command 不能为空"}
            output = runtime.exec(handle, command, timeout_seconds=120)
            return {"request_id": request_id, "ok": True, **output}
        if action == "finalize_task":
            test_command = str(tool_request.get("test_command") or "")
            test_result = runtime.exec(handle, test_command, timeout_seconds=600) if test_command else {"return_code": 1, "stderr": "任务未提供 test_command"}
            patch_result = runtime.exec(handle, "git diff --binary", timeout_seconds=30)
            resolved = test_result.get("return_code") == 0 and bool(str(patch_result.get("stdout") or "").strip())
            return {
                "request_id": request_id,
                "ok": True,
                "resolved": resolved,
                "test": test_result,
                "patch": str(patch_result.get("stdout") or ""),
            }
        if action == "stop_task":
            runtime.stop(handle)
            testbeds.pop(instance_id, None)
            return {"request_id": request_id, "ok": True}
        return {"request_id": request_id, "ok": False, "error": f"不支持的 SWE 工具类型：{action}"}


def _labels(request: ExecutionRequest, stage: str) -> dict[str, str]:
    return {
        "com.agent-bridge.managed": "true",
        "com.agent-bridge.evaluation.run_id": request.run_id,
        "com.agent-bridge.evaluation.execution_id": request.execution_id,
        "com.agent-bridge.evaluation.runner": "swebench",
        "com.agent-bridge.evaluation.stage": stage,
    }


def _read_new_requests(path: Path, seen_request_ids: set[str]) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    requests: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        request_id = str(payload.get("request_id") or "") if isinstance(payload, dict) else ""
        if request_id and request_id not in seen_request_ids:
            seen_request_ids.add(request_id)
            requests.append(payload)
    return requests


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
