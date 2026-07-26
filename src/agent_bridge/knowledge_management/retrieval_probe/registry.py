"""检索探测 adapter registry。"""

from __future__ import annotations

from .adapters import RetrievalProbeAdapter


class RetrievalProbeRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, RetrievalProbeAdapter] = {}

    def register(self, adapter: RetrievalProbeAdapter) -> None:
        if adapter.source_type in self._adapters:
            raise ValueError(
                f"duplicate retrieval probe adapter: {adapter.source_type}"
            )
        self._adapters[adapter.source_type] = adapter

    def list(self) -> tuple[RetrievalProbeAdapter, ...]:
        return tuple(self._adapters.values())
