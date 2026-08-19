from __future__ import annotations

import json
from pathlib import Path

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response

from agent_bridge.api.app import create_app
from agent_bridge.storage.sqlite import SQLiteStore
from agent_bridge.system_config.model_evaluation.runners.code import CodeRunner
from agent_bridge.system_config.model_evaluation.runners.opencompass import OpenCompassRunner
from agent_bridge.system_config.model_evaluation.runners.protocol import ExecutionRequest, container_log_tail
from agent_bridge.system_config.model_evaluation.runners.swebench import SWEbenchRunner
from agent_bridge.system_config.model_evaluation.runtimes import BindMount, ContainerHandle, ContainerSpec, DockerCliRuntime
from agent_bridge.system_config.model_evaluation.service import ModelEvaluationService


def test_model_evaluation_runtime_requires_docker_images(wm_paths) -> None:
    app = create_app(wm_paths, admins={"root"})
    with TestClient(app) as client:
        headers = {"X-Agent-Bridge-User": "root"}
        response = client.get("/api/v1/model-evaluations/runtime", headers=headers)
        assert response.status_code == 200
        payload = response.json()
        assert payload["configured"] is False
        assert payload["runtime"] == "docker"
        assert "Docker" in payload["message"]

        datasets = client.get("/api/v1/model-evaluations/datasets", headers=headers)
        assert datasets.status_code == 200
        assert {item["key"] for item in datasets.json()} == {
            "ceval_gen", "mmlu_pro_gen", "gsm8k_chat_gen", "ifeval_gen", "humaneval", "mbpp", "swebench_lite"
        }

        started = client.post(
            "/api/v1/model-evaluations",
            headers=headers,
            json={"model_name": "example", "datasets": ["gsm8k_chat_gen"]},
        )
        assert started.status_code == 400
        assert "Docker" in started.json()["detail"]

        invalid_samples = client.post(
            "/api/v1/model-evaluations",
            headers=headers,
            json={"model_name": "example", "datasets": ["gsm8k_chat_gen"], "max_samples": 0},
        )
        assert invalid_samples.status_code == 422


def test_model_evaluation_config_limits_dataset_group_to_requested_samples() -> None:
    config = ModelEvaluationService._render_config(
        "example-model",
        ["ceval_gen", "mmlu_pro_gen", "gsm8k_chat_gen", "ifeval_gen"],
        20,
    )
    assert "dataset_groups = [('ceval_gen', 'ceval_datasets'), ('mmlu_pro_gen', 'mmlu_pro_datasets')" in config
    assert "class AgentBridgeSampleDatasetReader" not in config
    assert "base_count, remainder = divmod(20, len(group_datasets))" in config
    assert "sample_count=sample_count, sample_mode=\"head\", sample_seed=42" in config
    compile(config, "evaluation.py", "exec")


def test_model_evaluation_config_supports_seeded_random_sampling() -> None:
    config = ModelEvaluationService._render_config(
        "example-model",
        ["gsm8k_chat_gen"],
        20,
        sampling_mode="random",
        sample_seed=20260805,
    )
    assert "sample_count=sample_count, sample_mode=\"random\", sample_seed=20260805" in config
    assert "base_count, remainder = divmod(20, len(group_datasets))" in config
    assert "__import__('random').Random(f'20260805:{group_key}:allocation')" in config
    assert "random.Random(f'{sample_seed}:{sample_dataset_key}')" in OpenCompassRunner.render_sitecustomize()


@respx.mock
def test_model_evaluation_models_fall_back_to_public_model_config(wm_paths, monkeypatch) -> None:
    app = create_app(wm_paths, admins={"root"})
    with TestClient(app) as client:
        headers = {"X-Agent-Bridge-User": "root"}
        configured = client.put(
            "/api/v1/retrieval-probe/llm-config",
            headers=headers,
            json={"base_url": "https://llm.example/v1", "model": "probe", "api_key": "secret"},
        )
        assert configured.status_code == 200
        route = respx.get("https://llm.example/v1/models").mock(
            return_value=Response(200, json={"data": [{"id": "model-b"}, {"id": "model-a"}]})
        )
        response = client.post("/api/v1/model-evaluations/models", headers=headers, json={})
        assert response.status_code == 200
        assert response.json() == [{"id": "model-a", "label": "model-a"}, {"id": "model-b", "label": "model-b"}]
        assert route.called
        assert route.calls.last.request.headers["Authorization"] == "Bearer secret"


def test_model_evaluation_persists_runner_executions(wm_paths) -> None:
    store = SQLiteStore(wm_paths.db_path, wm_paths.log_db_path)
    store.init_schema()
    store.create_model_evaluation_run(
        run_id="eval_demo",
        model_name="demo",
        base_url="https://llm.example/v1",
        datasets=["gsm8k_chat_gen", "humaneval"],
        max_samples=10,
        sampling_mode="random",
        sample_seed=42,
        work_dir="/tmp/eval_demo",
        created_by="root",
        runtime="docker",
    )
    store.create_model_evaluation_execution(
        execution_id="exec_code",
        run_id="eval_demo",
        runner_key="code",
        datasets=["humaneval"],
        image="agent-bridge-agent-worker:latest",
        work_dir="/tmp/eval_demo/executions/code",
    )
    store.update_model_evaluation_execution(
        "exec_code",
        status="completed",
        container_id="container-123",
        result={"rows": [{"dataset": "humaneval", "metric": "pass@1", "score": "50.00"}]},
        started=True,
        finished=True,
    )
    executions = store.list_model_evaluation_executions("eval_demo")
    assert executions[0]["datasets"] == ["humaneval"]
    assert executions[0]["container_id"] == "container-123"
    assert executions[0]["result"]["rows"][0]["metric"] == "pass@1"


class _FinishedOpenCompassRuntime:
    def __init__(self) -> None:
        self.spec: ContainerSpec | None = None

    def run(self, spec: ContainerSpec, *, log_path: Path) -> ContainerHandle:
        self.spec = spec
        assert (spec.work_dir / "evaluation.py").is_file()
        assert (spec.work_dir / "sitecustomize.py").is_file()
        summary_dir = spec.work_dir / "output" / "summary"
        summary_dir.mkdir(parents=True)
        (summary_dir / "summary.csv").write_text("dataset,accuracy\ngsm8k,100.00\n", encoding="utf-8")
        return ContainerHandle(container_id="opencompass-1", image=spec.image, command=spec.command)

    def poll(self, handle: ContainerHandle) -> int | None:
        return 0

    def wait(self, handle: ContainerHandle) -> int:
        return 0

    def stop(self, handle: ContainerHandle) -> None:
        pass


def test_opencompass_runner_creates_work_dir_before_writing_config(tmp_path: Path) -> None:
    work_dir = tmp_path / "missing" / "opencompass"
    request = ExecutionRequest(
        run_id="run-1",
        execution_id="execution-1",
        work_dir=work_dir,
        model_name="demo",
        base_url="https://llm.example/v1",
        api_key="key",
        datasets=("gsm8k_chat_gen",),
        max_samples=1,
        sampling_mode="head",
        sample_seed=42,
        opencompass_image="opencompass:latest",
        agent_worker_image="worker:latest",
        swebench_manifest_path=tmp_path / "swebench-manifest.json",
    )
    runtime = _FinishedOpenCompassRuntime()

    result = OpenCompassRunner().execute(
        request,
        runtime,  # type: ignore[arg-type]
        report_container=lambda _handle: None,
        report_progress=lambda _message: None,
    )

    assert (work_dir / "evaluation.py").is_file()
    assert (work_dir / "sitecustomize.py").is_file()
    assert runtime.spec is not None
    assert runtime.spec.command[:2] == ("opencompass", "/workspace/evaluation.py")
    assert runtime.spec.environment["PYTHONPATH"] == "/workspace"
    assert result["rows"] == [{"dataset": "gsm8k", "accuracy": "100.00"}]


class _FinishedCodeRuntime:
    def __init__(self) -> None:
        self.spec: ContainerSpec | None = None

    def run(self, spec: ContainerSpec, *, log_path: Path) -> ContainerHandle:
        self.spec = spec
        assert (spec.work_dir / "request.json").is_file()
        (spec.work_dir / "generated-cases.json").write_text('{"cases": []}', encoding="utf-8")
        return ContainerHandle(container_id="code-1", image=spec.image, command=spec.command)

    def poll(self, handle: ContainerHandle) -> int | None:
        return 0

    def wait(self, handle: ContainerHandle) -> int:
        return 0

    def stop(self, handle: ContainerHandle) -> None:
        pass


def test_code_runner_creates_work_dir_before_writing_request(tmp_path: Path) -> None:
    work_dir = tmp_path / "missing" / "code"
    request = ExecutionRequest(
        run_id="run-1",
        execution_id="execution-1",
        work_dir=work_dir,
        model_name="demo",
        base_url="https://llm.example/v1",
        api_key="key",
        datasets=("humaneval", "mbpp"),
        max_samples=1,
        sampling_mode="head",
        sample_seed=42,
        opencompass_image="opencompass:latest",
        agent_worker_image="worker:latest",
        swebench_manifest_path=tmp_path / "swebench-manifest.json",
    )
    runtime = _FinishedCodeRuntime()

    result = CodeRunner().execute(
        request,
        runtime,  # type: ignore[arg-type]
        report_container=lambda _handle: None,
        report_progress=lambda _message: None,
    )

    assert (work_dir / "request.json").is_file()
    assert result["rows"] == [
        {"dataset": "humaneval", "metric": "pass@1", "score": "0.00"},
        {"dataset": "mbpp", "metric": "pass@1", "score": "0.00"},
    ]


class _FinishedSWEbenchRuntime:
    def __init__(self) -> None:
        self.spec: ContainerSpec | None = None

    def run(self, spec: ContainerSpec, *, log_path: Path) -> ContainerHandle:
        self.spec = spec
        (spec.work_dir / "result.json").write_text(
            json.dumps({"rows": [], "summary_found": False, "sample_manifests": [], "cases": []}), encoding="utf-8"
        )
        return ContainerHandle(container_id="worker-1", image=spec.image, command=spec.command)

    def poll(self, handle: ContainerHandle) -> int | None:
        return 0

    def wait(self, handle: ContainerHandle) -> int:
        return 0

    def stop(self, handle: ContainerHandle) -> None:
        pass


def _swebench_request(work_dir: Path, manifest_path: Path) -> ExecutionRequest:
    return ExecutionRequest(
        run_id="run-1",
        execution_id="execution-1",
        work_dir=work_dir,
        model_name="demo",
        base_url="http://host.docker.internal/v1",
        api_key="key",
        datasets=("swebench_lite",),
        max_samples=1,
        sampling_mode="head",
        sample_seed=42,
        opencompass_image="opencompass:latest",
        agent_worker_image="worker:latest",
        swebench_manifest_path=manifest_path,
    )


def test_swebench_runner_requires_runtime_manifest(tmp_path: Path) -> None:
    runtime = _FinishedSWEbenchRuntime()
    with pytest.raises(RuntimeError, match="SWE-bench manifest 未就绪"):
        SWEbenchRunner().execute(
            _swebench_request(tmp_path / "work", tmp_path / "missing.json"),
            runtime,  # type: ignore[arg-type]
            report_container=lambda _handle: None,
            report_progress=lambda _message: None,
        )
    assert runtime.spec is None


def test_swebench_runner_mounts_runtime_manifest_read_only(tmp_path: Path) -> None:
    manifest_path = tmp_path / "swebench-manifest.json"
    manifest_path.write_text('{"version":"test","tasks":[{"instance_id":"case-1"}]}', encoding="utf-8")
    runtime = _FinishedSWEbenchRuntime()

    result = SWEbenchRunner().execute(
        _swebench_request(tmp_path / "work", manifest_path),
        runtime,  # type: ignore[arg-type]
        report_container=lambda _handle: None,
        report_progress=lambda _message: None,
    )

    assert result["summary_found"] is False
    assert runtime.spec is not None
    assert runtime.spec.bind_mounts == (
        BindMount(
            source=manifest_path,
            target="/opt/agent-bridge-data/swebench/swebench-manifest.json",
            read_only=True,
        ),
    )


def test_docker_runtime_passes_read_only_bind_mount(tmp_path: Path, monkeypatch) -> None:
    runtime = DockerCliRuntime(docker_bin="docker")
    commands: list[list[str]] = []

    class _Process:
        def poll(self) -> None:
            return None

    def fake_popen(command: list[str], **kwargs: object) -> _Process:
        commands.append(command)
        return _Process()

    monkeypatch.setattr("agent_bridge.system_config.model_evaluation.runtimes.docker.subprocess.Popen", fake_popen)
    monkeypatch.setattr(runtime, "_wait_for_cidfile", lambda cidfile, process: "container-1")
    manifest_path = tmp_path / "swebench-manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")

    runtime.run(
        ContainerSpec(
            image="worker:latest",
            command=("swe-agent",),
            work_dir=tmp_path / "work",
            labels={},
            bind_mounts=(BindMount(manifest_path, "/opt/manifest.json", read_only=True),),
        ),
        log_path=tmp_path / "runner.log",
    )

    command = commands[0]
    assert command[:4] == ["docker", "run", "--rm", "--cidfile"]
    assert Path(command[4]).parent == tmp_path / "work"
    assert command[5:] == [
        "--mount", f"type=bind,src={tmp_path / 'work'},dst=/workspace",
        "--mount", f"type=bind,src={manifest_path},dst=/opt/manifest.json,readonly",
        "--workdir", "/workspace", "--network", "bridge", "worker:latest", "swe-agent",
    ]


def test_docker_runtime_opens_mounted_workspace_for_container_writes(tmp_path: Path, monkeypatch) -> None:
    runtime = DockerCliRuntime(docker_bin="docker")

    class _Process:
        def poll(self) -> None:
            return None

    monkeypatch.setattr("agent_bridge.system_config.model_evaluation.runtimes.docker.subprocess.Popen", lambda command, **kwargs: _Process())
    monkeypatch.setattr(runtime, "_wait_for_cidfile", lambda cidfile, process: "container-1")

    mounted_dir = tmp_path / "mounted"
    runtime.run(
        ContainerSpec(image="worker:latest", command=("swe-agent",), work_dir=mounted_dir, labels={}),
        log_path=mounted_dir / "runner.log",
    )
    assert mounted_dir.stat().st_mode & 0o7777 == 0o1777

    unmounted_dir = tmp_path / "unmounted"
    runtime.run(
        ContainerSpec(image="worker:latest", command=("sleep",), work_dir=unmounted_dir, labels={}, mount_workspace=False),
        log_path=unmounted_dir / "runner.log",
    )
    assert unmounted_dir.stat().st_mode & 0o7777 != 0o1777


def test_container_log_tail_reports_missing_and_truncates_long_logs(tmp_path: Path) -> None:
    missing = container_log_tail(tmp_path / "missing.log")
    assert "无法读取日志" in missing

    empty = tmp_path / "empty.log"
    empty.write_text("", encoding="utf-8")
    assert "日志为空" in container_log_tail(empty)

    long_log = tmp_path / "runner.log"
    long_log.write_text("STARTMARK" + "x" * 6000 + "tail-end", encoding="utf-8")
    summary = container_log_tail(long_log)
    assert summary.startswith(f"日志 {long_log} 尾部内容：\n")
    assert summary.endswith("tail-end")
    assert "STARTMARK" not in summary


class _FailingContainerRuntime:
    """模拟容器输出日志后退出的运行时。"""

    def __init__(self, *, log_name: str, log_text: str, return_code: int = 1) -> None:
        self._log_name = log_name
        self._log_text = log_text
        self._return_code = return_code

    def run(self, spec: ContainerSpec, *, log_path: Path) -> ContainerHandle:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(self._log_text, encoding="utf-8")
        assert log_path.name == self._log_name
        return ContainerHandle(container_id="failed-1", image=spec.image, command=spec.command)

    def poll(self, handle: ContainerHandle) -> int:
        return self._return_code

    def wait(self, handle: ContainerHandle) -> int:
        return self._return_code

    def stop(self, handle: ContainerHandle) -> None:
        pass


def _execution_request(work_dir: Path, manifest_path: Path, datasets: tuple[str, ...]) -> ExecutionRequest:
    return ExecutionRequest(
        run_id="run-1",
        execution_id="execution-1",
        work_dir=work_dir,
        model_name="demo",
        base_url="https://llm.example/v1",
        api_key="key",
        datasets=datasets,
        max_samples=1,
        sampling_mode="head",
        sample_seed=42,
        opencompass_image="opencompass:latest",
        agent_worker_image="worker:latest",
        swebench_manifest_path=manifest_path,
    )


def test_opencompass_runner_failure_includes_log_tail(tmp_path: Path) -> None:
    request = _execution_request(tmp_path / "work", tmp_path / "swebench-manifest.json", ("gsm8k_chat_gen",))
    runtime = _FailingContainerRuntime(
        log_name="runner.log",
        log_text="Traceback (most recent call last):\nPermissionError: [Errno 13] Permission denied: '/workspace/output'",
    )

    with pytest.raises(RuntimeError) as excinfo:
        OpenCompassRunner().execute(
            request,
            runtime,  # type: ignore[arg-type]
            report_container=lambda _handle: None,
            report_progress=lambda _message: None,
        )

    message = str(excinfo.value)
    assert "OpenCompass 容器退出，退出码 1" in message
    assert "Permission denied: '/workspace/output'" in message
    assert str(request.work_dir / "runner.log") in message


def test_code_runner_generation_failure_includes_log_tail(tmp_path: Path) -> None:
    request = _execution_request(tmp_path / "work", tmp_path / "swebench-manifest.json", ("humaneval",))
    runtime = _FailingContainerRuntime(log_name="generation.log", log_text="ImportError: No module named 'requests'")

    with pytest.raises(RuntimeError) as excinfo:
        CodeRunner().execute(
            request,
            runtime,  # type: ignore[arg-type]
            report_container=lambda _handle: None,
            report_progress=lambda _message: None,
        )

    message = str(excinfo.value)
    assert "代码生成容器执行失败" in message
    assert "No module named 'requests'" in message


def test_swebench_runner_failure_includes_log_tail(tmp_path: Path) -> None:
    manifest_path = tmp_path / "swebench-manifest.json"
    manifest_path.write_text('{"version":"test","tasks":[]}', encoding="utf-8")
    request = _execution_request(tmp_path / "work", manifest_path, ("swebench_lite",))
    runtime = _FailingContainerRuntime(log_name="runner.log", log_text="ERROR: agent crashed")

    with pytest.raises(RuntimeError) as excinfo:
        SWEbenchRunner().execute(
            request,
            runtime,  # type: ignore[arg-type]
            report_container=lambda _handle: None,
            report_progress=lambda _message: None,
        )

    message = str(excinfo.value)
    assert "SWE-bench Agent 容器执行失败" in message
    assert "agent crashed" in message
