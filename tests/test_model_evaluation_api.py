from __future__ import annotations

import respx
from fastapi.testclient import TestClient
from httpx import Response

from agent_bridge.api.app import create_app
from agent_bridge.system_config.model_evaluation.service import ModelEvaluationService


def test_model_evaluation_runtime_reports_independent_runner_requirement(wm_paths, monkeypatch) -> None:
    monkeypatch.delenv("AGENT_BRIDGE_OPENCOMPASS_BIN", raising=False)
    app = create_app(wm_paths, admins={"root"})
    with TestClient(app) as client:
        headers = {"X-Agent-Bridge-User": "root"}
        response = client.get("/model-evaluations/runtime", headers=headers)
        assert response.status_code == 200
        payload = response.json()
        assert payload["configured"] is False
        assert "PageIndex" in payload["message"]
        assert "AGENT_BRIDGE_OPENCOMPASS_BIN" in payload["configure_command"]

        datasets = client.get("/model-evaluations/datasets", headers=headers)
        assert datasets.status_code == 200
        assert {item["key"] for item in datasets.json()} == {"demo_gsm8k_chat_gen", "demo_math_chat_gen"}

        started = client.post(
            "/model-evaluations",
            headers=headers,
            json={"model_name": "example", "datasets": ["demo_gsm8k_chat_gen"]},
        )
        assert started.status_code == 400
        assert "评估运行时未配置" in started.json()["detail"]

        invalid_samples = client.post(
            "/model-evaluations",
            headers=headers,
            json={"model_name": "example", "datasets": ["demo_gsm8k_chat_gen"], "max_samples": 0},
        )
        assert invalid_samples.status_code == 422


def test_model_evaluation_config_limits_each_dataset_to_requested_samples() -> None:
    config = ModelEvaluationService._render_config(
        "example-model",
        ["demo_gsm8k_chat_gen", "demo_math_chat_gen"],
        20,
    )
    assert "datasets = gsm8k_datasets + math_datasets" in config
    assert "class AgentBridgeSampleDatasetReader(DatasetReader):" in config
    assert "dataset_base.DatasetReader = AgentBridgeSampleDatasetReader" in config
    assert "sample_count=20, sample_mode=\"head\", sample_seed=42" in config
    compile(config, "evaluation.py", "exec")


def test_model_evaluation_config_supports_seeded_random_sampling() -> None:
    config = ModelEvaluationService._render_config(
        "example-model",
        ["demo_gsm8k_chat_gen"],
        20,
        sampling_mode="random",
        sample_seed=20260805,
    )
    assert "sample_count=20, sample_mode=\"random\", sample_seed=20260805" in config
    assert "random.Random(f'{sample_seed}:{sample_dataset_key}')" in config


@respx.mock
def test_model_evaluation_models_fall_back_to_public_model_config(wm_paths, monkeypatch) -> None:
    monkeypatch.delenv("AGENT_BRIDGE_OPENCOMPASS_BIN", raising=False)
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
