from __future__ import annotations

from typing import Any

from agent_bridge.knowledge_management.code_knowledge.scheduler import CodeGraphScheduler
from agent_bridge.knowledge_management.code_knowledge.understand_scheduler import UnderstandingScheduler
from agent_bridge.knowledge_management.docs_knowledge.doc_sync_scheduler import DocSyncScheduler
from agent_bridge.system_config.plugin_update_scheduler import PluginUpdateScheduler


class _SyncConfigStore:
    def get_sync_config(self) -> dict[str, str]:
        return {
            "code_sync_cron": "0 * * * *",
            "understand_cron": "0 2 * * *",
            "doc_sync_cron": "*/30 * * * *",
            "ua_git_url": "",
            "ua_plugin_update_cron": "0 3 * * 0",
            "claude_mem_git_url": "",
            "claude_mem_plugin_update_cron": "30 3 * * 0",
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


def test_understanding_scheduler_logs_failed_runs(caplog) -> None:
    class Service:
        def analyze_understand(self, actor: str, repo_key: str) -> dict[str, Any]:
            assert actor == "root"
            assert repo_key == "agent-bridge"
            return {"success": False, "error": "分析超时（600s）", "node_count": 0, "edge_count": 0}

    scheduler = UnderstandingScheduler(Service(), _CodeStore(), {"root"})
    scheduler.start()
    try:
        with caplog.at_level("WARNING"):
            scheduler._run_understand("agent-bridge")
        status = scheduler.get_status()
    finally:
        scheduler.stop()

    progress = status["jobs"][0]["progress"]
    assert progress["status"] == "failed"
    assert progress["message"] == "代码理解失败"
    assert progress["error"] == "分析超时（600s）"
    assert any("定时 Understand 分析失败 agent-bridge" in record.message for record in caplog.records)


def test_plugin_update_scheduler_schedules_configured_plugins() -> None:
    class Store(_SyncConfigStore):
        def get_sync_config(self) -> dict[str, str]:
            config = super().get_sync_config()
            config.update({
                "ua_git_url": "https://example.test/ua.git",
                "claude_mem_git_url": "https://example.test/claude-mem.git",
            })
            return config

    class Service:
        def update_understand_plugin(self, actor: str) -> dict[str, str]:
            assert actor == "root"
            return {"status": "updated", "message": "ua updated"}

        def update_claude_mem_plugin(self, actor: str) -> dict[str, str]:
            assert actor == "root"
            return {"status": "updated", "message": "memory updated"}

    scheduler = PluginUpdateScheduler(Service(), Store(), {"root"})
    scheduler.start()
    try:
        status = scheduler.get_status()
        assert [job["plugin_key"] for job in status["jobs"]] == ["understand-anything", "claude-mem"]
        scheduler._run_plugin_update("claude-mem")
        progress = scheduler.get_status()["jobs"][1]["progress"]
    finally:
        scheduler.stop()

    assert progress["status"] == "succeeded"
    assert progress["message"] == "memory updated"
