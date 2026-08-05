"""基于 OpenCompass 的 OpenAI 兼容模型评估服务。"""

from __future__ import annotations

import csv
import json
import logging
import os
import subprocess
import threading
import uuid
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

import httpx

from agent_bridge.core.config import AgentBridgePaths
from agent_bridge.core.domain import NotFound, ValidationError, require_admin_user

logger = logging.getLogger(__name__)


DATASETS = {
    "demo_gsm8k_chat_gen": {
        "label": "GSM8K（数学推理，Demo）",
        "description": "小规模小学数学推理样例，适合先验证模型与接口。",
    },
    "demo_math_chat_gen": {
        "label": "MATH（数学推理，Demo）",
        "description": "小规模竞赛数学样例，适合快速评估推理能力。",
    },
}


class ModelEvaluationService:
    """保存评测元数据，并在隔离的 OpenCompass 子进程中运行评测。"""

    def __init__(self, *, paths: AgentBridgePaths, store, admins: set[str]) -> None:
        self._store = store
        self._admins = admins
        self._run_root = paths.run_dir / "model-evaluations"
        self._process_lock = threading.Lock()
        self._active_processes: dict[str, subprocess.Popen] = {}
        self._stopped_run_ids: set[str] = set()

    def list_datasets(self, actor: str) -> list[dict[str, str]]:
        require_admin_user(actor, self._admins)
        return [{"key": key, **value} for key, value in DATASETS.items()]

    def runtime_status(self, actor: str) -> dict[str, Any]:
        require_admin_user(actor, self._admins)
        executable = self._runner_executable()
        if executable:
            return {"configured": True, "executable": str(executable), "message": "OpenCompass 独立评估运行时已配置"}
        return {
            "configured": False,
            "executable": None,
            "message": "OpenCompass 评估运行时未配置。为避免与 PageIndex 的 httpx 依赖冲突，主服务不会安装它。",
            "install_command": "./scripts/install_model_evaluation_runner.sh /opt/agent-bridge-opencompass",
            "configure_command": "export AGENT_BRIDGE_OPENCOMPASS_BIN=/opt/agent-bridge-opencompass/bin/opencompass",
        }

    def list_models(self, actor: str, *, base_url: str = "", api_key: str = "") -> list[dict[str, str]]:
        require_admin_user(actor, self._admins)
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
        require_admin_user(actor, self._admins)
        return [self._public_run(run) for run in self._store.list_model_evaluation_runs(limit=max(1, min(limit, 100)))]

    def get_run(self, actor: str, run_id: str) -> dict[str, Any]:
        require_admin_user(actor, self._admins)
        run = self._store.get_model_evaluation_run(run_id)
        if run is None:
            raise NotFound(f"未找到模型评估任务: {run_id}")
        return self._public_run(run)

    def start_run(
        self,
        actor: str,
        *,
        model_name: str,
        datasets: list[str],
        max_samples: int = 64,
        base_url: str = "",
        api_key: str = "",
    ) -> dict[str, Any]:
        require_admin_user(actor, self._admins)
        self._require_runner()
        cleaned_model = model_name.strip()
        if not cleaned_model:
            raise ValidationError("请选择待评估模型")
        selected_datasets = list(dict.fromkeys(item.strip() for item in datasets if item.strip()))
        unsupported = set(selected_datasets) - set(DATASETS)
        if not selected_datasets or unsupported:
            raise ValidationError("请至少选择一个受支持的简单数据集")
        if not 1 <= max_samples <= 1000:
            raise ValidationError("每个数据集最多题数必须在 1 到 1000 之间")
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
            work_dir=str(work_dir),
            created_by=actor,
        )
        thread = threading.Thread(
            target=self._run_opencompass,
            args=(run_id, work_dir, cleaned_model, endpoint, key, selected_datasets, max_samples),
            name=f"model-evaluation-{run_id[-8:]}",
            daemon=True,
        )
        thread.start()
        logger.info(
            "模型评估任务已创建 run_id=%s model=%s datasets=%s max_samples=%d",
            run_id,
            cleaned_model,
            selected_datasets,
            max_samples,
        )
        return self._public_run(run)

    def recover_interrupted_runs(self) -> int:
        count = self._store.abandon_model_evaluation_runs()
        if count:
            logger.warning("已标记服务重启中断的模型评估任务 count=%d", count)
        return count

    def stop_all(self) -> None:
        """终止当前进程启动的评测子进程，避免服务退出后遗留计费任务。"""
        with self._process_lock:
            processes = list(self._active_processes.items())
            self._stopped_run_ids.update(run_id for run_id, _ in processes)
        for run_id, process in processes:
            if process.poll() is not None:
                continue
            logger.warning("正在终止服务退出中的模型评估 run_id=%s pid=%s", run_id, process.pid)
            process.terminate()
        for run_id, process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("模型评估进程未及时退出，强制终止 run_id=%s pid=%s", run_id, process.pid)
                process.kill()

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
    def _auth_headers(api_key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {api_key}"} if api_key else {}

    @staticmethod
    def _runner_executable() -> Path | None:
        configured = os.environ.get("AGENT_BRIDGE_OPENCOMPASS_BIN", "").strip()
        if not configured:
            return None
        candidate = Path(configured).expanduser()
        return candidate if candidate.is_file() and os.access(candidate, os.X_OK) else None

    def _require_runner(self) -> Path:
        executable = self._runner_executable()
        if executable is None:
            raise ValidationError(
                "OpenCompass 评估运行时未配置。请在独立 venv/Docker 安装 opencompass[api]，"
                "设置 AGENT_BRIDGE_OPENCOMPASS_BIN 后重启服务；不要安装到 Agent Bridge 主环境。"
            )
        return executable

    @staticmethod
    def _public_run(run: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in run.items()
            if key != "work_dir"
        } | {"output_ref": f"model-evaluations/{run['run_id']}"}

    def _run_opencompass(
        self,
        run_id: str,
        work_dir: Path,
        model_name: str,
        base_url: str,
        api_key: str,
        datasets: list[str],
        max_samples: int,
    ) -> None:
        self._store.update_model_evaluation_run(
            run_id, status="running", progress_message="正在准备 OpenCompass 配置", started=True
        )
        try:
            executable = self._require_runner()
            config_path = work_dir / "evaluation.py"
            config_path.write_text(self._render_config(model_name, datasets, max_samples), encoding="utf-8")
            log_path = work_dir / "opencompass.log"
            environment = os.environ.copy()
            environment["OPENAI_API_KEY"] = api_key
            environment["OPENAI_BASE_URL"] = base_url
            self._store.update_model_evaluation_run(run_id, progress_message="OpenCompass 正在执行推理与评分")
            with log_path.open("w", encoding="utf-8") as log_file:
                process = subprocess.Popen(
                    [executable, str(config_path), "-w", str(work_dir / "output")],
                    cwd=work_dir,
                    env=environment,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                with self._process_lock:
                    self._active_processes[run_id] = process
                return_code = process.wait()
            with self._process_lock:
                stopped = run_id in self._stopped_run_ids
            if stopped:
                self._store.update_model_evaluation_run(
                    run_id,
                    status="abandoned",
                    progress_message="服务停止导致评估中断",
                    error="服务停止导致评估进程中断",
                    finished=True,
                )
                return
            if return_code != 0:
                raise RuntimeError(f"OpenCompass 进程退出，退出码 {return_code}；请查看服务端评估日志")
            result = self._collect_results(work_dir / "output")
            self._store.update_model_evaluation_run(
                run_id,
                status="completed",
                progress_message="评估完成",
                result=result,
                finished=True,
            )
            logger.info("模型评估完成 run_id=%s model=%s", run_id, model_name)
        except Exception as exc:
            logger.exception("模型评估失败 run_id=%s model=%s", run_id, model_name)
            self._store.update_model_evaluation_run(
                run_id,
                status="failed",
                progress_message="评估失败",
                error=str(exc),
                finished=True,
            )
        finally:
            with self._process_lock:
                self._active_processes.pop(run_id, None)
                self._stopped_run_ids.discard(run_id)

    @staticmethod
    def _render_config(model_name: str, datasets: list[str], max_samples: int) -> str:
        imports = []
        dataset_names = []
        if "demo_gsm8k_chat_gen" in datasets:
            imports.append("from opencompass.configs.datasets.demo.demo_gsm8k_chat_gen import gsm8k_datasets")
            dataset_names.append("gsm8k_datasets")
        if "demo_math_chat_gen" in datasets:
            imports.append("from opencompass.configs.datasets.demo.demo_math_chat_gen import math_datasets")
            dataset_names.append("math_datasets")
        model_literal = json.dumps(model_name, ensure_ascii=False)
        return "\n".join([
            "from mmengine.config import read_base",
            "from opencompass.models import OpenAI",
            "",
            "with read_base():",
            *[f"    {line}" for line in imports],
            "",
            f"datasets = {' + '.join(dataset_names)}",
            f"for dataset in datasets:\n    dataset['reader_cfg']['test_range'] = '[0:{max_samples}]'",
            "api_meta_template = dict(round=[dict(role='HUMAN', api_role='HUMAN'), dict(role='BOT', api_role='BOT', generate=True)])",
            "models = [dict(",
            f"    abbr={model_literal}, type=OpenAI, path={model_literal}, key='ENV',",
            "    openai_api_base=__import__('os').environ['OPENAI_BASE_URL'].rstrip('/') + '/chat/completions',",
            "    meta_template=api_meta_template, query_per_second=1, max_out_len=1024, max_seq_len=4096, batch_size=1,",
            ")]",
            "",
        ])

    @staticmethod
    def _collect_results(output_dir: Path) -> dict[str, Any]:
        rows: list[dict[str, str]] = []
        seen: set[Path] = set()
        for pattern in ("summary/*.csv", "**/summary*.csv"):
            for csv_path in output_dir.glob(pattern):
                if csv_path in seen:
                    continue
                seen.add(csv_path)
                try:
                    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
                        rows.extend({key: value or "" for key, value in row.items()} for row in csv.DictReader(handle))
                except (OSError, UnicodeError, csv.Error):
                    logger.warning("读取 OpenCompass 结果文件失败 path=%s", csv_path, exc_info=True)
        return {"rows": rows, "summary_found": bool(rows)}
