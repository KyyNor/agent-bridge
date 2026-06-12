from __future__ import annotations

from typing import Any

from agent_bridge.codegraph.scheduler import CodeGraphScheduler
from agent_bridge.codegraph.understand_scheduler import UnderstandingScheduler
from agent_bridge.knowledge.doc_sync_scheduler import DocSyncScheduler


class _SyncConfigStore:
    def get_sync_config(self) -> dict[str, str]:
        return {
            "code_sync_cron": "0 * * * *",
            "understand_cron": "0 2 * * *",
            "doc_sync_cron": "*/30 * * * *",
        }


class _CodeStore(_SyncConfigStore):
    def list_code_repositories(self) -> list[dict[str, Any]]:
        return [
            {
                "repo_key": "agent-bridge",
                "status": "active",
                "auto_understand": True,
            }
        ]


def test_doc_sync_scheduler_records_job_progress() -> None:
    class Service:
        def sync(self, actor: str, all_users: bool, progress_callback) -> dict[str, int]:
            assert actor == "root"
            assert all_users is True
            progress_callback({"total": 2, "processed": 0, "succeeded": 0, "failed": 0})
            progress_callback({
                "total": 2,
                "processed": 1,
                "succeeded": 1,
                "failed": 0,
                "current_job": {"doc_slug": "guide", "backend_slug": "mock"},
            })
            return {"processed": 2, "succeeded": 2, "failed": 0}

    scheduler = DocSyncScheduler(Service(), _SyncConfigStore(), {"root"})

    scheduler._run_sync()
    status = scheduler.get_status()

    assert status["last_run"]["status"] == "succeeded"
    assert status["last_run"]["processed"] == 2
    assert status["last_run"]["succeeded"] == 2
    assert status["last_run"]["failed"] == 0
    assert status["current_run"] is None


def test_code_sync_scheduler_exposes_latest_run_progress_in_status() -> None:
    class Service:
        def sync_repository(self, actor: str, repo_key: str) -> dict[str, int]:
            assert actor == "root"
            assert repo_key == "agent-bridge"
            return {"indexed": 42}

    scheduler = CodeGraphScheduler(Service(), _CodeStore(), {"root"})
    scheduler.start()
    try:
        scheduler._sync_repo("agent-bridge")
        status = scheduler.get_status()
    finally:
        scheduler.stop()

    progress = status["jobs"][0]["progress"]
    assert progress["status"] == "succeeded"
    assert progress["message"] == "同步完成，索引 42 项"
    assert progress["finished_at"] is not None


def test_understanding_scheduler_exposes_latest_run_progress_in_status() -> None:
    class Service:
        def analyze_understand(self, actor: str, repo_key: str) -> dict[str, Any]:
            assert actor == "root"
            assert repo_key == "agent-bridge"
            return {"success": True, "node_count": 7, "edge_count": 9}

    scheduler = UnderstandingScheduler(Service(), _CodeStore(), {"root"})
    scheduler.start()
    try:
        scheduler._run_understand("agent-bridge")
        status = scheduler.get_status()
    finally:
        scheduler.stop()

    progress = status["jobs"][0]["progress"]
    assert progress["status"] == "succeeded"
    assert progress["message"] == "理解完成，节点 7，边 9"
    assert progress["error"] is None
