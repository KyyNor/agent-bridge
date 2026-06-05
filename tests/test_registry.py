from __future__ import annotations

from pathlib import Path

import pytest

from agent_bridge.core.config import BackendConfig, AgentBridgePaths
from agent_bridge.knowledge.backends.mock import MockBackend
from agent_bridge.knowledge.backends.ragflow import RagFlowBackend
from agent_bridge.knowledge.backends.registry import BackendRegistry, create_registry
from agent_bridge.knowledge.backends.weknora import WeknoraBackend


def test_registry_from_empty_config(tmp_path: Path):
    paths = AgentBridgePaths.from_root(tmp_path)
    registry = create_registry(paths)
    assert registry.backends == {}


def test_registry_from_mock_config(tmp_path: Path):
    config = BackendConfig(slug="mock", backend_type="mock")
    registry = BackendRegistry({"mock": config}, paths=tmp_path)
    adapter = registry.get("mock")
    assert adapter is not None
    assert isinstance(adapter, MockBackend)


def test_registry_unknown_backend_type(tmp_path: Path):
    config = BackendConfig(slug="unknown", backend_type="nonexistent")
    with pytest.raises(ValueError, match="unknown backend type"):
        BackendRegistry({"unknown": config}, paths=tmp_path)


def test_registry_get_missing_slug(tmp_path: Path):
    config = BackendConfig(slug="mock", backend_type="mock")
    registry = BackendRegistry({"mock": config}, paths=tmp_path)
    assert registry.get("nonexistent") is None


def test_registry_list_slugs(tmp_path: Path):
    configs = {
        "mock": BackendConfig(slug="mock", backend_type="mock"),
    }
    registry = BackendRegistry(configs, paths=tmp_path)
    assert registry.list_slugs() == ["mock"]


def test_registry_with_ragflow_config(tmp_path: Path):
    config = BackendConfig(
        slug="ragflow",
        backend_type="ragflow",
        base_url="http://localhost:9380",
        api_key="test-key",
        timeout=30,
    )
    registry = BackendRegistry({"ragflow": config}, paths=tmp_path)
    adapter = registry.get("ragflow")
    assert adapter is not None
    assert isinstance(adapter, RagFlowBackend)


def test_registry_with_weknora_config(tmp_path: Path):
    config = BackendConfig(
        slug="weknora",
        backend_type="weknora",
        base_url="http://localhost",
        api_key="test-key",
        timeout=30,
        embedding_model_id="emb-1",
        summary_model_id="chat-1",
    )
    registry = BackendRegistry({"weknora": config}, paths=tmp_path)
    adapter = registry.get("weknora")
    assert adapter is not None
    assert isinstance(adapter, WeknoraBackend)
    assert adapter.embedding_model_id == "emb-1"
    assert adapter.summary_model_id == "chat-1"
