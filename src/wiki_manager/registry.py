from __future__ import annotations

from pathlib import Path

from wiki_manager.config import BackendConfig, WikiManagerPaths, load_backend_configs
from wiki_manager.domain import BackendAdapter
from wiki_manager.mock_backend import MockBackend


ADAPTER_CLASSES: dict[str, type] = {
    "mock": MockBackend,
    "ragflow": type("RagFlowBackend", (), {}),  # placeholder; real class used via lazy import
}


class BackendRegistry:
    def __init__(self, configs: dict[str, BackendConfig], paths: Path) -> None:
        self._adapters: dict[str, BackendAdapter] = {}
        for slug, config in configs.items():
            adapter_cls = ADAPTER_CLASSES.get(config.backend_type)
            if adapter_cls is None:
                raise ValueError(f"unknown backend type: {config.backend_type}")
            if config.backend_type == "mock":
                self._adapters[slug] = adapter_cls(paths / "data" / "backend" / "mock")
            elif config.backend_type == "ragflow":
                from wiki_manager.ragflow_backend import RagFlowBackend

                self._adapters[slug] = RagFlowBackend(
                    base_url=config.base_url or "",
                    api_key=config.api_key or "",
                    timeout=config.timeout,
                )
            else:
                self._adapters[slug] = adapter_cls()

    def get(self, slug: str) -> BackendAdapter | None:
        return self._adapters.get(slug)

    def list_slugs(self) -> list[str]:
        return sorted(self._adapters.keys())

    @property
    def backends(self) -> dict[str, BackendAdapter]:
        return dict(self._adapters)


def create_registry(paths: WikiManagerPaths) -> BackendRegistry:
    configs = load_backend_configs(paths)
    if not configs:
        return BackendRegistry({}, paths.root)
    config_map = {c.slug: c for c in configs}
    return BackendRegistry(config_map, paths.root)
