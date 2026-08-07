from __future__ import annotations

import respx
from fastapi.testclient import TestClient
from httpx import Response

from agent_bridge.api.app import create_app
from agent_bridge.storage.sqlite import SQLiteStore
from agent_bridge.system_config.model_evaluation.service import ModelEvaluationService


def test_model_evaluation_runtime_requires_docker_images(wm_paths) -> None:
    app = create_app(wm_paths, admins={"root"})
    with TestClient(app) as client:
        headers = {"X-Agent-Bridge-User": "root"}
        response = client.get("/model-evaluations/runtime", headers=headers)
        assert response.status_code == 200
        payload = response.json()
        assert payload["configured"] is False
        assert payload["runtime"] == "docker"
        assert "Docker" in payload["message"]

        datasets = client.get("/model-evaluations/datasets", headers=headers)
        assert datasets.status_code == 200
        assert {item["key"] for item in datasets.json()} == {
            "ceval_gen", "mmlu_pro_gen", "gsm8k_chat_gen", "ifeval_gen", "humaneval", "mbpp", "swebench_lite"
        }

        started = client.post(
            "/model-evaluations",
            headers=headers,
            json={"model_name": "example", "datasets": ["gsm8k_chat_gen"]},
        )
        assert started.status_code == 400
        assert "Docker" in started.json()["detail"]

        invalid_samples = client.post(
            "/model-evaluations",
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
    assert "class AgentBridgeSampleDatasetReader(DatasetReader):" in config
    assert "dataset_base.DatasetReader = AgentBridgeSampleDatasetReader" in config
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
    assert "random.Random(f'{sample_seed}:{sample_dataset_key}')" in config


@respx.mock
def test_model_evaluation_models_fall_back_to_public_model_config(wm_paths, monkeypatch) -> None:
    app = create_app(wm_paths, admins={"root"})
    with TestClient(app) as client:
        headers = {"X-Agent-Bridge-User": "root"}
        configured = client.put(
            "/retrieval-probe/llm-config",
            headers=headers,
            json={"base_url": "https://llm.example/v1", "model": "probe", "api_key": "secret"},
        )
        assert configured.status_code == 200
        route = respx.get("https://llm.example/v1/models").mock(
            return_value=Response(200, json={"data": [{"id": "model-b"}, {"id": "model-a"}]})
        )
        response = client.post("/model-evaluations/models", headers=headers, json={})
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
