"""TDD tests for memory block deletion.

Contract (per the approved plan):
- admin can delete a memory block -> row gone; profile bindings get block_key SET NULL (FK)
- claude-mem worker is stopped and its data_dir is removed
- non-admin denied; missing block raises NotFound
"""
from __future__ import annotations

from typing import Any

import pytest

from agent_bridge.app.service import AgentBridgeService
from agent_bridge.core.domain import AccessDenied, NotFound


class FakeWorker:
    """Records stop_dashboard calls; mimics ClaudeMemWorkerService surface."""

    def __init__(self) -> None:
        self.stopped: list[str] = []

    def stop_dashboard(self, block: dict[str, Any]) -> dict[str, Any]:
        self.stopped.append(block["block_key"])
        return {"stopped": True}

    # untouched surfaces required by MemoryService wiring
    def search(self, *a, **k): ...  # noqa: E704
    def timeline(self, *a, **k): ...  # noqa: E704
    def get_observation(self, *a, **k): ...  # noqa: E704
    def health(self, block): ...  # noqa: E704
    def dashboard_status(self, block): ...  # noqa: E704
    def start_dashboard(self, block): ...  # noqa: E704
    def touch_dashboard(self, block): ...  # noqa: E704


def _service(wm_paths):
    service = AgentBridgeService.create(wm_paths, admins={"root"})
    service.init_system()
    service.memory.worker_service = FakeWorker()
    return service


def test_delete_block_requires_admin(wm_paths) -> None:
    service = _service(wm_paths)
    service.memory.create_block("root", "dev-memory", "Dev Memory", "")
    with pytest.raises(AccessDenied):
        service.memory.delete_block("alice", "dev-memory")


def test_delete_block_missing_raises_not_found(wm_paths) -> None:
    service = _service(wm_paths)
    with pytest.raises(NotFound):
        service.memory.delete_block("root", "missing")


def test_delete_block_removes_row_stops_worker_and_cleans_data_dir(wm_paths) -> None:
    service = _service(wm_paths)
    service.memory.create_block("root", "dev-memory", "Dev Memory", "")
    block = service.store.memory.get_memory_block("dev-memory")
    data_dir = wm_paths.data_dir / "claude-mem" / "blocks" / "dev-memory"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "obs.jsonl").write_text("{}", encoding="utf-8")

    service.memory.delete_block("root", "dev-memory")

    assert service.store.memory.get_memory_block("dev-memory") is None
    assert service.memory.worker_service.stopped == ["dev-memory"]
    assert not data_dir.exists()


def test_delete_block_sets_profile_binding_block_key_to_null(wm_paths) -> None:
    service = _service(wm_paths)
    service.governance.upsert_profile("root", "dev", "Dev", "", "active")
    service.memory.create_block("root", "dev-memory", "Dev Memory", "")
    service.memory.set_profile_binding("root", "dev", "dev-memory", enabled=True)
    binding = service.store.memory.get_profile_memory_binding("dev")
    assert binding["block_key"] == "dev-memory"

    service.memory.delete_block("root", "dev-memory")

    # FK ON DELETE SET NULL -> binding row remains but block_key is null
    binding_after = service.store.memory.get_profile_memory_binding("dev")
    assert binding_after is not None
    assert binding_after["block_key"] is None
