"""文档同步任务执行器。

从 ``AgentBridgeService`` 抽出的同步引擎：消费 sync_jobs 队列、执行单条
任务、对后端 KB 丢失做自动恢复，并通过 ``progress_callback`` 上报进度。

门面保留 ``sync`` 薄转发以维持 API 与测试兼容；``_run_job`` 也保留转发，
因为单测会直接调用 ``service._run_job(job)``。
"""

from __future__ import annotations

import logging
from threading import Lock
from typing import Any, Callable, Protocol

from agent_bridge.core.domain import (
    BackendDocStatus,
    DocumentReconciliationBackend,
    DocumentStatusUncertain,
    NotFound,
    Operation,
    SyncJobStatus,
    SyncStateStatus,
    require_admin_user,
)
from agent_bridge.knowledge_management.docs_knowledge.status_reconciliation import (
    DocumentStatusReconciler,
    SyncUploadSource,
    needs_upload_check,
)
from agent_bridge.storage.sqlite import SQLiteStore

logger = logging.getLogger(__name__)


class _FacadeCallbacks(Protocol):
    """门面对 runner 暴露的最小回调表面。"""

    def get_adapter(self, slug: str): ...
    def align_backends(
        self, kb_id: int | None = None, backend: str | None = None
    ) -> list[tuple[int, str]]: ...


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
        self._lock = Lock()
        self._reconciler = DocumentStatusReconciler(store)

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
        with self._lock:
            return self._sync(backend, progress_callback, kb_id)

    def reconcile(self, eligible_targets: list[tuple[int, str]]) -> None:
        if self._lock.acquire(blocking=False):
            try:
                self._reconcile(eligible_targets, include_failed=True)
            finally:
                self._lock.release()

    def _reconcile(self, eligible_targets: list[tuple[int, str]], *, include_failed: bool) -> None:
        adapters = {}
        for slug in {slug for _, slug in eligible_targets}:
            try:
                adapter = self._facade.get_adapter(slug)
            except NotFound:
                logger.warning("文档状态核对跳过未配置的后端 backend=%s", slug)
                continue
            if isinstance(adapter, DocumentReconciliationBackend):
                adapters[slug] = adapter
        self._reconciler.check(
            {(kb_id, slug): adapters[slug] for kb_id, slug in eligible_targets if slug in adapters},
            include_failed=include_failed,
        )

    def _sync(
        self,
        backend: str | None,
        progress_callback: Callable[[dict[str, Any]], None] | None,
        kb_id: int | None,
    ) -> dict[str, int]:
        # A target may have been created by an older migration without its
        # remote ID.  Repair it before taking the job snapshot so that the
        # sync path never needs to use the local KB slug as a remote ID.
        eligible_targets = self._facade.align_backends(kb_id=kb_id, backend=backend)
        self._reconcile(eligible_targets, include_failed=False)
        jobs = self.store.list_runnable_jobs(
            actor=None,
            backend_slug=backend,
            kb_id=kb_id,
            eligible_targets=eligible_targets,
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
                    eligible_targets=[(job["kb_id"], job["backend_slug"])],
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
        source: SyncUploadSource | None = None
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

                version = self.store.get_current_document_version(job["doc_id"])
                if version is None:
                    raise NotFound("document archive not found")
                folder_path = placement.get("folder_path") or ""
                source = SyncUploadSource.from_version(version, placement)
                remote_path = source.remote_path if supports_folders else None
                tracks_status = isinstance(adapter, DocumentReconciliationBackend)
                observation = BackendDocStatus(status="pending") if tracks_status else None
                matched = None
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
                        file_path=source.file_path,
                        filename=source.filename,
                        remote_path=remote_path,
                    )
                elif tracks_status:
                    if needs_upload_check(job, (previous_sync_state or {}).get("backend_status")):
                        matched = self._reconciler.find_uploaded_document(
                            adapter, job, backend_kb_id, source,
                        )
                    if matched is not None:
                        backend_doc_id = matched.id
                        observation = matched.status
                    else:
                        backend_doc_id = adapter.upload_tracked(
                            backend_kb_id, source.file_path, source.remote_path,
                            lambda fingerprint: self._reconciler.record_upload_attempt(job, source, fingerprint),
                        )
                else:
                    upload_kwargs: dict[str, Any] = {
                        "backend_kb_id": backend_kb_id,
                        "doc_slug": job["doc_slug"],
                        "file_path": source.file_path,
                        "filename": source.filename,
                    }
                    if supports_folders:
                        upload_kwargs["remote_path"] = remote_path
                    backend_doc_id = adapter.upload(**upload_kwargs)

                key = (job["doc_id"], job["kb_id"], job["backend_slug"])
                with self.store.transaction():
                    if matched is not None and (
                        self.store.get_current_document_version(job["doc_id"]) != version
                        or self.store.get_document_placement(job["doc_id"], job["kb_id"]) != placement
                    ):
                        raise DocumentStatusUncertain("核对期间文档版本或目录发生变化")
                    if supports_folders:
                        self.store.upsert_backend_folder_mapping(
                            job["kb_id"], job["backend_slug"], placement["folder_id"],
                            folder_path, folder_path, status="synced", error=None,
                        )
                    if observation is not None:
                        self._reconciler.save_remote_status(
                            key, backend_doc_id, observation, source.version_id, recovered=matched is not None,
                        )
                    else:
                        self.store.upsert_sync_state(*key, backend_doc_id, SyncStateStatus.synced)
                    if matched is not None:
                        logger.info("重试前已核对远端上传 job=%s backend=%s 状态=%s", job["id"], backend, observation.status)
                    self.store.update_job_status(job["id"], SyncJobStatus.succeeded)
            if job["operation"] == "delete":
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
            upload_uncertain = isinstance(exc, DocumentStatusUncertain) and op in {
                Operation.create.value, Operation.update.value,
            }
            failed_backend_doc_id = (
                previous_sync_state.get("backend_doc_id")
                if (op == Operation.move.value or upload_uncertain) and previous_sync_state
                else None
            )
            with self.store.transaction():
                self.store.upsert_sync_state(
                    job["doc_id"],
                    job["kb_id"],
                    job["backend_slug"],
                    failed_backend_doc_id,
                    failed_status,
                    backend_status="unconfirmed" if upload_uncertain else None,
                    backend_error=str(exc),
                    status_version_id=source.version_id if upload_uncertain and source else None,
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
