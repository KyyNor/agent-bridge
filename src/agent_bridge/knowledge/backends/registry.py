from __future__ import annotations

from pathlib import Path

from agent_bridge.core.config import BackendConfig, AgentBridgePaths, load_backend_configs
from agent_bridge.core.domain import BackendAdapter
from agent_bridge.knowledge.backends.mock import MockBackend


ADAPTER_CLASSES: dict[str, type] = {
    "mock": MockBackend,
    "ragflow": type("RagFlowBackend", (), {}),  # placeholder; real class used via lazy import
    "weknora": type("WeknoraBackend", (), {}),  # placeholder; real class used via lazy import
}


class BackendRegistry:
    def __init__(self, configs: dict[str, BackendConfig], paths: Path) -> None:
        self._paths = paths
        self._adapters: dict[str, BackendAdapter] = {}
        for slug, config in configs.items():
            adapter = self._create_adapter(config)
            if adapter is not None:
                self._adapters[slug] = adapter

    def _create_adapter(self, config: BackendConfig) -> BackendAdapter | None:
        if config.backend_type == "mock":
            return MockBackend(self._paths / "data" / "backend" / "mock")
        elif config.backend_type == "ragflow":
            from agent_bridge.knowledge.backends.ragflow import RagFlowBackend
            return RagFlowBackend(
                base_url=config.base_url or "",
                api_key=config.api_key or "",
                timeout=config.timeout,
            )
        elif config.backend_type == "weknora":
            from agent_bridge.knowledge.backends.weknora import WeknoraBackend
            return WeknoraBackend(
                base_url=config.base_url or "",
                api_key=config.api_key or "",
                timeout=config.timeout,
                embedding_model_id=config.embedding_model_id,
                summary_model_id=config.summary_model_id,
            )
        return None

    def get(self, slug: str) -> BackendAdapter | None:
        return self._adapters.get(slug)

    def list_slugs(self) -> list[str]:
        return sorted(self._adapters.keys())

    @property
    def backends(self) -> dict[str, BackendAdapter]:
        return dict(self._adapters)

    def add_backend(self, config: BackendConfig) -> BackendAdapter | None:
        adapter = self._create_adapter(config)
        if adapter is not None:
            self._adapters[config.slug] = adapter
        return adapter

    def remove_backend(self, slug: str) -> None:
        self._adapters.pop(slug, None)

    def update_backend(self, config: BackendConfig) -> BackendAdapter | None:
        self.remove_backend(config.slug)
        return self.add_backend(config)


def create_registry(paths: AgentBridgePaths) -> BackendRegistry:
    configs = load_backend_configs(paths)
    if not configs:
        return BackendRegistry({}, paths.root)
    config_map = {c.slug: c for c in configs}
    return BackendRegistry(config_map, paths.root)


def create_registry_from_db(paths: AgentBridgePaths, store: Any) -> BackendRegistry:
    """Build a registry from DB-stored backend configurations."""
    rows = store.list_backends()
    if not rows:
        return BackendRegistry({}, paths.root)
    config_map: dict[str, BackendConfig] = {}
    for row in rows:
        config_map[row["slug"]] = BackendConfig(
            slug=row["slug"],
            backend_type=row["backend_type"],
            base_url=row.get("base_url"),
            api_key=row.get("api_key"),
            timeout=row.get("timeout", 120),
            embedding_model_id=row.get("embedding_model_id"),
            summary_model_id=row.get("summary_model_id"),
        )
    return BackendRegistry(config_map, paths.root)
