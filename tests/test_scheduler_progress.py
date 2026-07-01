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

    def list_all_active_repo_sources(self) -> list[dict[str, Any]]:
        return []


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


def test_code_sync_scheduler_stop_terminates_active_processes() -> None:
    stopped = []

    class Service:
        def stop_active_processes(self) -> None:
            stopped.append(True)

    scheduler = CodeGraphScheduler(Service(), _CodeStore(), {"root"})

    scheduler.start()
    scheduler.stop()

    assert stopped == [True]


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


class _RepoSourceStore(_SyncConfigStore):
    """fake store:list 两个源,记录 mark_kb_repo_source_sync 的失败调用。"""

    def __init__(self) -> None:
        self.marked_errors: list[tuple[Any, str, str]] = []  # (kb_id, repo_key, error)

    def list_all_active_repo_sources(self) -> list[dict[str, Any]]:
        return [
            {"kb_id": 1, "kb_slug": "kb-ok", "repo_key": "r-ok"},
            {"kb_id": 2, "kb_slug": "kb-bad", "repo_key": "r-bad"},
        ]

    def mark_kb_repo_source_sync(self, kb_id, repo_key, *, success, error=None) -> None:
        if not success:
            self.marked_errors.append((kb_id, repo_key, error))


def test_run_sync_runs_git_diff_phase_before_drain() -> None:
    calls: list[str] = []

    class Service:
        def sync_kb_repo_source_changes(self, actor, kb_slug, repo_key):
            calls.append(f"diff:{kb_slug}:{repo_key}")

        def sync(self, actor, all_users, progress_callback):
            calls.append("drain")
            return {"processed": 0, "succeeded": 0, "failed": 0}

    store = _RepoSourceStore()
    scheduler = DocSyncScheduler(Service(), store, {"root"})
    scheduler._run_sync()
    # git diff 阶段先于 drain
    assert calls.index("diff:kb-ok:r-ok") < calls.index("drain")
    assert calls.index("diff:kb-bad:r-bad") < calls.index("drain")


def test_run_sync_isolates_failing_source_and_still_drains() -> None:
    class Service:
        def sync_kb_repo_source_changes(self, actor, kb_slug, repo_key):
            if repo_key == "r-bad":
                raise RuntimeError("boom")

        def sync(self, actor, all_users, progress_callback):
            return {"processed": 0, "succeeded": 0, "failed": 0}

    store = _RepoSourceStore()
    scheduler = DocSyncScheduler(Service(), store, {"root"})
    scheduler._run_sync()
    # 失败源被记录
    assert (2, "r-bad", "boom") in store.marked_errors
    # 不影响整体 status(仍 succeeded)
    assert scheduler.get_status()["last_run"]["status"] == "succeeded"
