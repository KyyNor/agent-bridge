"""文档同步任务执行器。

从 ``AgentBridgeService`` 抽出的同步引擎：消费 sync_jobs 队列、执行单条
任务、对后端 KB 丢失做自动恢复，并通过 ``progress_callback`` 上报进度。

门面保留 ``sync`` 薄转发以维持 API 与测试兼容；``_run_job`` 也保留转发，
因为单测会直接调用 ``service._run_job(job)``。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Protocol

from agent_bridge.app.document_paths import (
    join_backend_path,
    normalize_relative_document_path,
)
from agent_bridge.core.domain import (
    NotFound,
    Operation,
    SyncJobStatus,
    SyncStateStatus,
    require_admin_user,
)
from agent_bridge.storage.sqlite import SQLiteStore

logger = logging.getLogger(__name__)


class _FacadeCallbacks(Protocol):
    """门面对 runner 暴露的最小回调表面。"""

    def get_adapter(self, slug: str): ...
    def align_backends(self, kb_id: int | None = None) -> None: ...


class SyncJobRunner:
    """文档同步任务循环与单任务执行器。"""

    def __init__(
        self,
        *,
        store: SQLiteStore,
        admins: set[str],
        facade: _FacadeCallbacks,
    ) -> None:
        self.store = store
        self.admins = admins
        self._facade = facade

    def sync(
        self,
        actor: str,
        all_users: bool,
        backend: str | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        kb_id: int | None = None,
    ) -> dict[str, int]:
        if kb_id is None:
            require_admin_user(actor, self.admins)
        # A target may have been created by an older migration without its
        # remote ID.  Repair it before taking the job snapshot so that the
        # sync path never needs to use the local KB slug as a remote ID.
        self._facade.align_backends(kb_id=kb_id)
        jobs = self.store.list_runnable_jobs(
            actor=None,
            backend_slug=backend,
            kb_id=kb_id,
        )
        logger.info("文档同步: %d 个待处理任务", len(jobs))
        succeeded = 0
        failed = 0
        recovery_backend_kbs: dict[tuple[int, str], str] = {}
        recovered_job_keys: set[tuple[int, int, str]] = set()
        processed_job_keys: set[tuple[int, int, str]] = set()
        pending_jobs = list(jobs)
        if progress_callback:
            progress_callback({"event": "start", "total": len(jobs), "processed": 0, "succeeded": 0, "failed": 0})
        while pending_jobs:
            job = pending_jobs.pop(0)
            job_key = (job["doc_id"], job["kb_id"], job["backend_slug"])
            if job_key in processed_job_keys:
                continue
            if progress_callback:
                progress_callback({
                    "event": "job_start",
                    "total": len(jobs),
                    "processed": len(processed_job_keys),
                    "succeeded": succeeded,
                    "failed": failed,
                    "current_job": self._sync_job_progress_payload(job),
                })
            ok = self._run_job(job, recovery_backend_kbs, recovered_job_keys)
            if job_key in recovered_job_keys:
                # rebuild_backend_target replaces the whole runnable queue for
                # this KB/backend.  Put the replacement jobs at the front so
                # this sync processes them before stale snapshot entries; the
                # group key prevents those stale entries from being run again.
                recovered_job_keys.remove(job_key)
                refreshed_jobs = self.store.list_runnable_jobs(
                    actor=None,
                    backend_slug=backend,
                    kb_id=kb_id,
                )
                for refreshed_job in reversed(refreshed_jobs):
                    if (
                        refreshed_job["kb_id"] != job["kb_id"]
                        or refreshed_job["backend_slug"] != job["backend_slug"]
                    ):
                        continue
                    refreshed_key = (
                        refreshed_job["doc_id"],
                        refreshed_job["kb_id"],
                        refreshed_job["backend_slug"],
                    )
                    if refreshed_key not in processed_job_keys:
                        pending_jobs.insert(0, refreshed_job)
                continue

            processed_job_keys.add(job_key)
            if ok:
                succeeded += 1
            else:
                failed += 1
            if progress_callback:
                progress_callback({
                    "event": "job_done",
                    "total": len(jobs),
                    "processed": len(processed_job_keys),
                    "succeeded": succeeded,
                    "failed": failed,
                    "current_job": self._sync_job_progress_payload(job),
                })
        logger.info("文档同步完成: %d 成功, %d 失败", succeeded, failed)
        if progress_callback:
            progress_callback({"event": "finish", "total": len(jobs), "processed": len(processed_job_keys), "succeeded": succeeded, "failed": failed})
        return {"processed": len(processed_job_keys), "succeeded": succeeded, "failed": failed}

    @staticmethod
    def _is_kb_gone(exc: Exception) -> bool:
        msg = str(exc).lower()
        if "knowledge base not found" in msg:
            return True
        if "404" in msg and "1003" in msg:          # Weknora
            return True
        if "ragflow" in msg and "404" in msg:       # RagFlow HTTP 404
            return True
        return False

    def _run_job(
        self,
        job: dict[str, Any],
        recovery_backend_kbs: dict[tuple[int, str], str] | None = None,
        recovered_job_keys: set[tuple[int, int, str]] | None = None,
    ) -> bool:
        doc_title = job.get("doc_title", job.get("doc_slug", "?"))
        backend = job.get("backend_slug", "?")
        op = job.get("operation", "?")
        logger.info("文档同步任务 #%d: %s '%s' -> %s", job["id"], op, doc_title, backend)
        self.store.update_job_status(job["id"], SyncJobStatus.running)
        adapter = None
        supports_folders = False
        placement: dict[str, Any] | None = None
        previous_sync_state = self.store.get_sync_state(
            job["doc_id"], job["kb_id"], job["backend_slug"]
        )
        try:
            adapter = self._facade.get_adapter(job["backend_slug"])
            supports_folders = self._backend_supports_folders(adapter)
            recovery_key = (job["kb_id"], job["backend_slug"])
            backend_kb_id = (
                recovery_backend_kbs.get(recovery_key)
                if recovery_backend_kbs is not None
                else None
            ) or job.get("backend_kb_id")
            if not backend_kb_id:
                raise RuntimeError(
                    f"backend target '{job['backend_slug']}' has no remote knowledge-base ID"
                )
            if job["operation"] == "delete":
                backend_doc_id = previous_sync_state["backend_doc_id"] if previous_sync_state else None
                if backend_doc_id:
                    adapter.delete(backend_kb_id, backend_doc_id)
                self.store.upsert_sync_state(
                    job["doc_id"],
                    job["kb_id"],
                    job["backend_slug"],
                    None,
                    SyncStateStatus.deleted,
                )
            else:
                placement = self.store.get_document_placement(job["doc_id"], job["kb_id"])
                if placement is None:
                    raise NotFound("document knowledge-base placement not found")

                archive_path = job.get("archive_path")
                filename = job.get("original_filename") or job["doc_slug"]
                current_document = self.store.get_document_by_id(job["doc_id"], include_deleted=True)
                if current_document and current_document.get("current_version_id"):
                    current_version = next(
                        (
                            version
                            for version in self.store.list_versions(job["doc_id"])
                            if version["id"] == current_document["current_version_id"]
                        ),
                        None,
                    )
                    if current_version is not None:
                        archive_path = current_version["archive_path"]
                        filename = current_version["original_filename"]
                if not archive_path:
                    raise NotFound("document archive not found")

                folder_path = placement.get("folder_path") or ""
                normalized_filename = normalize_relative_document_path(filename)
                upload_filename = Path(normalized_filename).name
                remote_filename = (
                    normalized_filename
                    if placement.get("archive_entry_id") is not None
                    else upload_filename
                )
                remote_path = (
                    join_backend_path(folder_path, remote_filename)
                    if supports_folders
                    else None
                )
                if job["operation"] == Operation.move.value:
                    old_backend_doc_id = (
                        previous_sync_state.get("backend_doc_id") if previous_sync_state else None
                    )
                    if not old_backend_doc_id:
                        raise RuntimeError("cannot move document without an existing backend document")
                    move_method = getattr(adapter, "move", None) or getattr(adapter, "relocate", None)
                    if not callable(move_method):
                        raise RuntimeError(f"backend '{job['backend_slug']}' does not implement move")
                    backend_doc_id = move_method(
                        backend_kb_id=backend_kb_id,
                        backend_doc_id=old_backend_doc_id,
                        file_path=Path(archive_path),
                        filename=upload_filename,
                        remote_path=remote_path,
                    )
                else:
                    upload_kwargs: dict[str, Any] = {
                        "backend_kb_id": backend_kb_id,
                        "doc_slug": job["doc_slug"],
                        "file_path": Path(archive_path),
                        "filename": upload_filename,
                    }
                    if supports_folders:
                        upload_kwargs["remote_path"] = remote_path
                    backend_doc_id = adapter.upload(**upload_kwargs)

                if supports_folders:
                    self.store.upsert_backend_folder_mapping(
                        job["kb_id"],
                        job["backend_slug"],
                        placement["folder_id"],
                        folder_path,
                        folder_path,
                        status="synced",
                        error=None,
                    )
                self.store.upsert_sync_state(
                    job["doc_id"],
                    job["kb_id"],
                    job["backend_slug"],
                    backend_doc_id,
                    SyncStateStatus.synced,
                )
            self.store.update_job_status(job["id"], SyncJobStatus.succeeded)
            logger.info("文档同步任务 #%d: 成功", job["id"])
            return True
        except Exception as exc:
            if (
                adapter is not None
                and op != Operation.move.value
                and self._is_kb_gone(exc)
                and job.get("kb_name")
                and job.get("kb_slug")
            ):
                logger.warning("文档同步任务 #%d: 后端 KB 已丢失，正在重建...", job["id"])
                try:
                    recovery_key = (job["kb_id"], job["backend_slug"])
                    new_id = (
                        recovery_backend_kbs.get(recovery_key)
                        if recovery_backend_kbs is not None
                        else None
                    )
                    if new_id is None:
                        new_id = adapter.create_kb(job["kb_slug"], job["kb_name"])
                        doc_count = self.store.rebuild_backend_target(
                            job["kb_id"], job["backend_slug"], new_id
                        )
                        if recovery_backend_kbs is not None:
                            recovery_backend_kbs[recovery_key] = new_id
                        if recovered_job_keys is not None:
                            recovered_job_keys.add(
                                (job["doc_id"], job["kb_id"], job["backend_slug"])
                            )
                    else:
                        doc_count = 0
                    self.store.update_job_status(job["id"], SyncJobStatus.succeeded)
                    logger.info("文档同步任务 #%d: 后端 KB 已重建，%d 个文档已重新调度", job["id"], doc_count)
                    return True
                except Exception as rebuild_exc:
                    logger.exception("文档同步任务 #%d: 重建失败 — %s", job["id"], rebuild_exc)
                    self.store.update_job_status(job["id"], SyncJobStatus.failed, error=str(exc))
                return False
            logger.exception("文档同步任务 #%d: 失败 — %s", job["id"], exc)
            failed_status = (
                SyncStateStatus.delete_failed if job["operation"] == "delete" else SyncStateStatus.sync_failed
            )
            if supports_folders and placement is not None:
                try:
                    folder_path = placement.get("folder_path") or ""
                    self.store.upsert_backend_folder_mapping(
                        job["kb_id"],
                        job["backend_slug"],
                        placement["folder_id"],
                        folder_path,
                        folder_path,
                        status="failed",
                        error=str(exc),
                    )
                except Exception:
                    logger.warning("保存后端目录映射失败: job=%s", job.get("id"), exc_info=True)
            failed_backend_doc_id = (
                previous_sync_state.get("backend_doc_id")
                if job["operation"] == Operation.move.value and previous_sync_state
                else None
            )
            self.store.upsert_sync_state(
                job["doc_id"],
                job["kb_id"],
                job["backend_slug"],
                failed_backend_doc_id,
                failed_status,
                backend_error=str(exc),
            )
            self.store.update_job_status(job["id"], SyncJobStatus.failed, error=str(exc))
            return False

    @staticmethod
    def _backend_supports_folders(adapter: Any) -> bool:
        return bool(adapter.capabilities().supports_folders)

    @staticmethod
    def _sync_job_progress_payload(job: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": job.get("id"),
            "operation": job.get("operation"),
            "backend_slug": job.get("backend_slug"),
            "kb_slug": job.get("kb_slug"),
            "doc_slug": job.get("doc_slug"),
            "doc_title": job.get("doc_title"),
        }
