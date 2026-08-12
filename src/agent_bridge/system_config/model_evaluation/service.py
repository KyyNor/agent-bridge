"""Docker 化模型评估的编排服务。"""

from __future__ import annotations

import logging
import os
import threading
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from agent_bridge.access_control.service import AccessControlService, ResourceScope
from agent_bridge.core.config import AgentBridgePaths
from agent_bridge.core.domain import NotFound, ValidationError, require_admin_user

from .runners import ExecutionRequest, OpenCompassRunner, RUNNERS
from .runtimes import ContainerHandle, ContainerRuntime, DockerCliRuntime
from .types import DATASETS_BY_KEY, DATASET_SPECS, DIMENSION_LABELS, DatasetSpec

logger = logging.getLogger(__name__)


class ModelEvaluationService:
    """创建父任务、调度 Docker runner，并归一化不同评测协议的结果。"""

    def __init__(
        self,
        *,
        paths: AgentBridgePaths,
        store,
        admins: set[str],
        runtime: ContainerRuntime | None = None,
        access: AccessControlService | None = None,
    ) -> None:
        self._store = store
        self._admins = admins
        self._access = access
        self._run_root = paths.run_dir / "model-evaluations"
        manifest_override = os.environ.get("AGENT_BRIDGE_EVAL_SWEBENCH_MANIFEST", "").strip()
        self._swebench_manifest_path = (
            Path(manifest_override).expanduser()
            if manifest_override
            else paths.data_dir / "model-evaluation" / "swebench-manifest.json"
        )
        self._runtime = runtime or DockerCliRuntime()
        self._process_lock = threading.Lock()
        self._active_containers: dict[str, ContainerHandle] = {}
        self._stopped_run_ids: set[str] = set()

    @staticmethod
    def _images() -> dict[str, str]:
        return {
            "opencompass": os.environ.get(
                "AGENT_BRIDGE_EVAL_OPENCOMPASS_IMAGE", "agent-bridge-opencompass-runner:latest"
            ).strip(),
            "agent_worker": os.environ.get(
                "AGENT_BRIDGE_EVAL_AGENT_WORKER_IMAGE", "agent-bridge-agent-worker:latest"
            ).strip(),
        }

    def list_datasets(self, actor: str) -> list[dict[str, Any]]:
        return [
            {
                "key": spec.key,
                "label": spec.label,
                "description": spec.description,
                "dimension": spec.dimension,
                "dimension_label": DIMENSION_LABELS[spec.dimension],
                "runner": spec.runner_key,
                "metric": spec.metric,
                "default_max_samples": spec.default_max_samples,
            }
            for spec in DATASET_SPECS
        ]

    def runtime_status(self, actor: str) -> dict[str, Any]:
        return self._runtime_status()

    def _runtime_status(self) -> dict[str, Any]:
        docker = self._runtime.status()
        images = self._images()
        image_status = {
            key: {"image": image, "available": bool(image) and self._runtime.image_exists(image)}
            for key, image in images.items()
        } if docker.get("available") else {
            key: {"image": image, "available": False} for key, image in images.items()
        }
        missing = [item["image"] for item in image_status.values() if not item["available"]]
        if not docker.get("available"):
            return {
                "configured": False,
                "runtime": "docker",
                "message": str(docker.get("message") or "Docker 不可用"),
                "docker": docker,
                "images": image_status,
            }
        if missing:
            return {
                "configured": False,
                "runtime": "docker",
                "message": f"评估 Docker 镜像未就绪：{', '.join(missing)}",
                "docker": docker,
                "images": image_status,
            }
        return {
            "configured": True,
            "runtime": "docker",
            "message": "Docker 评估运行时已就绪",
            "docker": docker,
            "images": image_status,
        }

    def list_models(self, actor: str, *, base_url: str = "", api_key: str = "") -> list[dict[str, str]]:
        endpoint, key = self._resolve_connection(base_url=base_url, api_key=api_key)
        try:
            with httpx.Client(timeout=15.0, follow_redirects=True) as client:
                response = client.get(f"{endpoint}/models", headers=self._auth_headers(key))
                response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("获取评估模型列表失败 base_url=%s error=%s", endpoint, exc)
            raise ValidationError("无法获取模型列表，请检查 Base URL、API Key 与 OpenAI 兼容接口") from exc
        payload = response.json()
        raw_models = payload.get("data", payload) if isinstance(payload, dict) else payload
        if not isinstance(raw_models, list):
            raise ValidationError("模型列表响应格式不符合 OpenAI 兼容接口约定")
        models = []
        for item in raw_models:
            identifier = item.get("id") if isinstance(item, dict) else str(item)
            if isinstance(identifier, str) and identifier.strip():
                models.append({"id": identifier.strip(), "label": identifier.strip()})
        return sorted(models, key=lambda item: item["id"].lower())

    def list_runs(self, actor: str, *, limit: int = 50) -> list[dict[str, Any]]:
        enforce_scope = self._access is not None and actor not in self._admins
        viewer_group_key = (
            self._access.actor_group_key(actor, required=True)
            if enforce_scope and self._access is not None
            else None
        )
        return [
            self._public_run(run)
            for run in self._store.list_model_evaluation_runs(
                limit=max(1, min(limit, 100)),
                viewer_group_key=viewer_group_key,
                enforce_scope=enforce_scope,
            )
        ]

    def get_run(self, actor: str, run_id: str) -> dict[str, Any]:
        run = self._store.get_model_evaluation_run(run_id)
        if run is None:
            raise NotFound(f"未找到模型评估任务: {run_id}")
        self._require_run_read(actor, run)
        return self._public_run(run)

    def start_run(
        self,
        actor: str,
        *,
        model_name: str,
        datasets: list[str],
        max_samples: int = 64,
        sampling_mode: str = "head",
        sample_seed: int = 42,
        base_url: str = "",
        api_key: str = "",
    ) -> dict[str, Any]:
        owner_group_key = (
            str(self._access.actor_group_key(actor, required=True))
            if self._access is not None
            else ""
        )
        self._require_runtime()
        cleaned_model = model_name.strip()
        if not cleaned_model:
            raise ValidationError("请选择待评估模型")
        selected_datasets = list(dict.fromkeys(item.strip() for item in datasets if item.strip()))
        unsupported = set(selected_datasets) - set(DATASETS_BY_KEY)
        if not selected_datasets or unsupported:
            raise ValidationError("请至少选择一个受支持的评估数据集")
        if not 1 <= max_samples <= 1000:
            raise ValidationError("每个数据集最多题数必须在 1 到 1000 之间")
        if sampling_mode not in {"head", "random"}:
            raise ValidationError("抽样方式仅支持固定前 N 条或随机抽样")
        if not 0 <= sample_seed <= 2_147_483_647:
            raise ValidationError("随机种子必须在 0 到 2147483647 之间")
        endpoint, key = self._resolve_connection(base_url=base_url, api_key=api_key)
        run_id = f"eval_{uuid.uuid4().hex}"
        work_dir = self._run_root / run_id
        work_dir.mkdir(parents=True, exist_ok=False)
        run = self._store.create_model_evaluation_run(
            run_id=run_id,
            model_name=cleaned_model,
            base_url=endpoint,
            datasets=selected_datasets,
            max_samples=max_samples,
            sampling_mode=sampling_mode,
            sample_seed=sample_seed,
            work_dir=str(work_dir),
            created_by=actor,
            runtime="docker",
            owner_group_key=owner_group_key,
        )
        images = self._images()
        grouped = self._group_datasets(selected_datasets)
        executions = []
        for runner_key, group in grouped.items():
            execution_id = f"exec_{uuid.uuid4().hex}"
            execution_dir = work_dir / "executions" / runner_key
            spec = DATASETS_BY_KEY[group[0]]
            image = images[spec.image_role]
            executions.append(self._store.create_model_evaluation_execution(
                execution_id=execution_id,
                run_id=run_id,
                runner_key=runner_key,
                datasets=group,
                image=image,
                work_dir=str(execution_dir),
            ))
        thread = threading.Thread(
            target=self._run_evaluation,
            args=(run_id, work_dir, cleaned_model, endpoint, key, max_samples, sampling_mode, sample_seed, executions),
            name=f"model-evaluation-{run_id[-8:]}",
            daemon=True,
        )
        thread.start()
        logger.info("模型评估任务已创建 run_id=%s model=%s datasets=%s", run_id, cleaned_model, selected_datasets)
        return self._public_run(run)

    def _require_run_read(self, actor: str, run: dict[str, Any]) -> None:
        if self._access is None:
            require_admin_user(actor, self._admins)
            return
        self._access.require_read(actor=actor, scope=ResourceScope.from_record(run))

    def recover_interrupted_runs(self) -> int:
        removed = self._runtime.cleanup_managed()
        if removed:
            logger.warning("已清理服务重启遗留的评估容器 count=%d", len(removed))
        count = self._store.abandon_model_evaluation_runs()
        if count:
            logger.warning("已标记服务重启中断的模型评估任务 count=%d", count)
        return count

    def stop_all(self) -> None:
        with self._process_lock:
            handles = list(self._active_containers.items())
            self._stopped_run_ids.update(item[0].split(":", 1)[0] for item in handles)
        for execution_id, handle in handles:
            logger.warning("正在终止服务退出中的模型评估 execution_id=%s container_id=%s", execution_id, handle.container_id)
            self._runtime.stop(handle)

    def _require_runtime(self) -> None:
        status = self._runtime_status()
        if not status["configured"]:
            raise ValidationError(str(status["message"]))

    def _run_evaluation(
        self,
        run_id: str,
        work_dir: Path,
        model_name: str,
        base_url: str,
        api_key: str,
        max_samples: int,
        sampling_mode: str,
        sample_seed: int,
        executions: list[dict[str, Any]],
    ) -> None:
        self._store.update_model_evaluation_run(run_id, status="running", progress_message="正在准备 Docker 评估任务", started=True)
        all_results: list[dict[str, Any]] = []
        failures: list[str] = []
        try:
            images = self._images()
            container_url = self._container_base_url(base_url)
            for execution in executions:
                execution_id = execution["execution_id"]
                runner_key = execution["runner_key"]
                self._store.update_model_evaluation_execution(execution_id, status="running", progress_message="正在准备容器", started=True)
                self._store.update_model_evaluation_run(run_id, progress_message=f"正在执行 {runner_key} 评测")
                request = ExecutionRequest(
                    run_id=run_id,
                    execution_id=execution_id,
                    work_dir=Path(execution["work_dir"]),
                    model_name=model_name,
                    base_url=container_url,
                    api_key=api_key,
                    datasets=tuple(execution["datasets"]),
                    max_samples=max_samples,
                    sampling_mode=sampling_mode,
                    sample_seed=sample_seed,
                    opencompass_image=images["opencompass"],
                    agent_worker_image=images["agent_worker"],
                    swebench_manifest_path=self._swebench_manifest_path,
                )
                runner = RUNNERS[runner_key]

                def report_container(handle: ContainerHandle, *, _execution_id: str = execution_id) -> None:
                    with self._process_lock:
                        self._active_containers[f"{run_id}:{_execution_id}:{handle.container_id}"] = handle
                    self._store.update_model_evaluation_execution(_execution_id, container_id=handle.container_id)

                def report_progress(message: str, *, _execution_id: str = execution_id) -> None:
                    self._store.update_model_evaluation_execution(_execution_id, progress_message=message)
                    self._store.update_model_evaluation_run(run_id, progress_message=message)

                try:
                    result = runner.execute(request, self._runtime, report_container=report_container, report_progress=report_progress)
                except Exception as exc:
                    logger.exception("模型评估子执行失败 run_id=%s execution_id=%s", run_id, execution_id)
                    message = str(exc)
                    failures.append(f"{runner_key}: {message}")
                    self._store.update_model_evaluation_execution(
                        execution_id, status="failed", progress_message="执行失败", error=message, finished=True
                    )
                else:
                    all_results.append({"execution_id": execution_id, "runner": runner_key, **result})
                    self._store.update_model_evaluation_execution(
                        execution_id, status="completed", progress_message="评估完成", result=result, finished=True
                    )
                finally:
                    with self._process_lock:
                        execution_prefix = f"{run_id}:{execution_id}:"
                        for key in [key for key in self._active_containers if key.startswith(execution_prefix)]:
                            self._active_containers.pop(key, None)
            stopped = run_id in self._stopped_run_ids
            if stopped:
                self._store.update_model_evaluation_run(
                    run_id, status="abandoned", progress_message="服务停止导致评估中断", error="服务停止导致评估容器中断", finished=True
                )
            elif failures and all_results:
                self._store.update_model_evaluation_run(
                    run_id,
                    status="completed_with_warnings",
                    progress_message="部分评估完成",
                    result=self._merge_results(all_results),
                    error="；".join(failures),
                    finished=True,
                )
            elif failures:
                self._store.update_model_evaluation_run(
                    run_id, status="failed", progress_message="评估失败", error="；".join(failures), finished=True
                )
            else:
                self._store.update_model_evaluation_run(
                    run_id, status="completed", progress_message="评估完成", result=self._merge_results(all_results), finished=True
                )
        finally:
            with self._process_lock:
                self._stopped_run_ids.discard(run_id)

    @staticmethod
    def _merge_results(results: list[dict[str, Any]]) -> dict[str, Any]:
        rows: list[dict[str, str]] = []
        manifests: list[dict[str, Any]] = []
        for result in results:
            rows.extend(result.get("rows") or [])
            manifests.extend(result.get("sample_manifests") or [])
        return {"rows": rows, "summary_found": bool(rows), "sample_manifests": manifests, "executions": results}

    @staticmethod
    def _group_datasets(datasets: list[str]) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = defaultdict(list)
        for dataset in datasets:
            grouped[DATASETS_BY_KEY[dataset].runner_key].append(dataset)
        return dict(grouped)

    def _resolve_connection(self, *, base_url: str, api_key: str) -> tuple[str, str]:
        supplied_url = base_url.strip()
        supplied_key = api_key.strip()
        if not supplied_url and not supplied_key:
            config = self._store.get_retrieval_probe_llm_config()
            supplied_url = str(config.get("base_url") or "").strip()
            supplied_key = str(config.get("api_key") or "").strip()
        if not supplied_url:
            raise ValidationError("请填写 Base URL，或先在公共模型配置中配置全量探测关键词模型")
        parsed = urlparse(supplied_url.rstrip("/"))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValidationError("Base URL 必须是 http 或 https 的完整地址")
        return supplied_url.rstrip("/"), supplied_key

    @staticmethod
    def _container_base_url(base_url: str) -> str:
        parsed = urlparse(base_url)
        if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            return base_url
        docker_host = os.environ.get("AGENT_BRIDGE_EVAL_DOCKER_HOST", "host.docker.internal")
        netloc = docker_host if parsed.port is None else f"{docker_host}:{parsed.port}"
        return urlunparse(parsed._replace(netloc=netloc))

    @staticmethod
    def _auth_headers(api_key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {api_key}"} if api_key else {}

    def _public_run(self, run: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value for key, value in run.items() if key != "work_dir"
        } | {
            "executions": self._store.list_model_evaluation_executions(run["run_id"]),
            "output_ref": f"model-evaluations/{run['run_id']}",
        }

    @staticmethod
    def _render_config(
        model_name: str,
        datasets: list[str],
        max_samples: int,
        sampling_mode: str = "head",
        sample_seed: int = 42,
    ) -> str:
        """兼容已有测试与外部脚本的配置渲染入口。"""
        return OpenCompassRunner.render_config(model_name, tuple(datasets), max_samples, sampling_mode, sample_seed)
