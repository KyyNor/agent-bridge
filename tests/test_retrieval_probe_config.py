from fastapi.testclient import TestClient

from agent_bridge.api.app import create_app
from agent_bridge.storage.sqlite import SQLiteStore


def test_retrieval_probe_llm_config_round_trip(wm_paths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    saved = store.save_retrieval_probe_llm_config(
        base_url="http://llm.test/v1",
        model="qwen2.5-3b-instruct",
        api_key="secret",
        clear_api_key=False,
    )
    assert saved["api_key"] == "secret"
    assert store.get_retrieval_probe_llm_config()["model"] == "qwen2.5-3b-instruct"


def test_llm_config_api_masks_preserves_and_clears_key(wm_paths) -> None:
    app = create_app(wm_paths, admins={"root"})
    with TestClient(app) as client:
        headers = {"X-Agent-Bridge-User": "root"}
        response = client.put("/api/v1/retrieval-probe/llm-config", headers=headers, json={
            "base_url": "http://llm.test/v1", "model": "small", "api_key": "secret",
        })
        assert response.status_code == 200
        assert response.json()["api_key_set"] is True
        assert "api_key" not in response.json()
        retained = client.put("/api/v1/retrieval-probe/llm-config", headers=headers, json={
            "base_url": "http://llm.test/v1", "model": "small", "api_key": "",
        })
        assert retained.json()["api_key_set"] is True
        cleared = client.put("/api/v1/retrieval-probe/llm-config", headers=headers, json={
            "base_url": "http://llm.test/v1", "model": "small", "clear_api_key": True,
        })
        assert cleared.json()["api_key_set"] is False
