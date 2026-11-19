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
    DocumentUploadFingerprint,
    NotFound,
    Operation,
    RemoteKnowledgeBaseMissing,
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
    def is_sync_target_enabled(self, kb_id: int, backend: str, remote_kb_id: str) -> bool: ...


class SyncJobDeferred(Exception):
    """目标或输入发生变化，保留任务待下一轮处理。"""


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
        self._reconciler = DocumentStatusReconciler(store, facade.is_sync_target_enabled)

    def sync(
        self,
        actor: str,
        all_users: bool,
        backend: str | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        kb_id: int | None = None,
        *,
        refresh_status: bool = True,
    ) -> dict[str, int]:
        if kb_id is None:
            require_admin_user(actor, self.admins)
        with self._lock:
            return self._sync(backend, progress_callback, kb_id, refresh_status=refresh_status)

    def reconcile(self, eligible_targets: list[tuple[int, str]]) -> None:
        with self._lock:
            self._reconcile(eligible_targets, include_failed=True)

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
        *,
        refresh_status: bool,
    ) -> dict[str, int]:
        # A target may have been created by an older migration without its
        # remote ID.  Repair it before taking the job snapshot so that the
        # sync path never needs to use the local KB slug as a remote ID.
        eligible_targets = self._facade.align_backends(kb_id=kb_id, backend=backend)
        if refresh_status:
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
            if ok is None:
                continue
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
        if isinstance(exc, RemoteKnowledgeBaseMissing):
            return True
        msg = str(exc).lower()
        if "knowledge base not found" in msg:
            return True
        if "404" in msg and "1003" in msg:          # Weknora
            return True
        if "ragflow" in msg and "404" in msg:       # RagFlow HTTP 404
            return True
        return False

    def _defer_job(self, job: dict[str, Any], reason: str) -> None:
        status = SyncJobStatus(job.get("job_status") or job.get("status") or SyncJobStatus.pending.value)
        self.store.update_job_status(job["id"], status, error=job.get("job_error") or job.get("error"))
        logger.info("文档同步任务 #%s 已延期 backend=%s 原因=%s", job["id"], job["backend_slug"], reason)

    def _run_job(
        self,
        job: dict[str, Any],
        recovery_backend_kbs: dict[tuple[int, str], str] | None = None,
        recovered_job_keys: set[tuple[int, int, str]] | None = None,
    ) -> bool | None:
        doc_title = job.get("doc_title", job.get("doc_slug", "?"))
        backend = job.get("backend_slug", "?")
        op = job.get("operation", "?")
        logger.info("文档同步任务 #%d: %s '%s' -> %s", job["id"], op, doc_title, backend)
        if not self.store.claim_document_sync_job(job["id"]):
            return None
        adapter = None
        backend_kb_id: str | None = None
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
            def check_target() -> None:
                if not self._facade.is_sync_target_enabled(job["kb_id"], backend, backend_kb_id):
                    raise SyncJobDeferred("同步目标已暂停、移除或变更")

            check_target()
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
                key = (job["doc_id"], job["kb_id"], backend)
                if op in {Operation.create.value, Operation.update.value} and job.get("version_id") != version["id"]:
                    latest = self.store.get_latest_document_sync_job(*key)
                    if (
                        latest and latest["id"] > job["id"] and latest["version_id"] == version["id"]
                        and latest["operation"] in {Operation.create.value, Operation.update.value}
                    ):
                        self.store.update_job_status(job["id"], SyncJobStatus.cancelled, error="已有当前版本的同步任务")
                        return None
                folder_path = placement.get("folder_path") or ""
                source = SyncUploadSource.from_version(version, placement)
                remote_path = source.remote_path if supports_folders else None
                tracks_status = isinstance(adapter, DocumentReconciliationBackend)
                observation = BackendDocStatus(status="pending") if tracks_status else None
                matched = None
                old_backend_doc_id = (
                    (previous_sync_state or {}).get("backend_doc_id") if op == Operation.move.value else None
                )

                def check_upload_source() -> None:
                    check_target()
                    if (
                        self.store.get_current_document_version(job["doc_id"]) != version
                        or self.store.get_document_placement(job["doc_id"], job["kb_id"]) != placement
                    ):
                        raise SyncJobDeferred("文档版本或目录已变更")

                if tracks_status:
                    if needs_upload_check(job, (previous_sync_state or {}).get("backend_status")):
                        matched = self._reconciler.find_uploaded_document(adapter, job, backend_kb_id, source)
                    if matched is not None:
                        backend_doc_id = matched.id
                        observation = matched.status
                    else:
                        check_upload_source()
                        if op == Operation.move.value and not old_backend_doc_id:
                            raise RuntimeError("cannot move document without an existing backend document")

                        def before_upload(fingerprint: DocumentUploadFingerprint) -> None:
                            with self.store.transaction():
                                check_upload_source()
                                self._reconciler.record_upload_attempt(job, source, fingerprint)

                        backend_doc_id = adapter.upload_tracked(
                            backend_kb_id, source.file_path, source.remote_path, before_upload,
                            replace_backend_doc_id=old_backend_doc_id,
                        )
                elif job["operation"] == Operation.move.value:
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

                with self.store.transaction():
                    if matched is not None:
                        check_upload_source()
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
                    self.store.cancel_duplicate_document_uploads(*key, source.version_id, job["id"])
            if job["operation"] == "delete":
                self.store.update_job_status(job["id"], SyncJobStatus.succeeded)
            logger.info("文档同步任务 #%d: 成功", job["id"])
            return True
        except SyncJobDeferred as exc:
            self._defer_job(job, str(exc))
            return None
        except Exception as exc:
            if (
                adapter is not None
                and backend_kb_id is not None
                and (op != Operation.move.value or isinstance(exc, RemoteKnowledgeBaseMissing))
                and self._is_kb_gone(exc)
                and job.get("kb_name")
                and job.get("kb_slug")
            ):
                if not self._facade.is_sync_target_enabled(job["kb_id"], backend, backend_kb_id):
                    self._defer_job(job, "同步目标已暂停、移除或变更")
                    return None
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
                Operation.create.value, Operation.update.value, Operation.move.value,
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
