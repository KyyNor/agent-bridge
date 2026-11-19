"""读取知识后端的解析状态，并找回应答丢失的上传。"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agent_bridge.app.document_paths import join_backend_path, normalize_relative_document_path
from agent_bridge.core.domain import (
    BackendDocStatus,
    DocumentReconciliationBackend,
    DocumentStatusUncertain,
    DocumentUploadFingerprint,
    MatchedBackendDocument,
    Operation,
    RemoteKnowledgeBaseMissing,
    SyncJobStatus,
    SyncStateStatus,
)
from agent_bridge.storage.sqlite import SQLiteStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SyncUploadSource:
    version_id: int
    file_path: Path
    filename: str
    remote_path: str

    @classmethod
    def from_version(cls, version: dict[str, Any], placement: dict[str, Any]) -> SyncUploadSource:
        relative_filename = normalize_relative_document_path(version["original_filename"])
        filename = Path(relative_filename).name
        remote_name = relative_filename if placement.get("archive_entry_id") is not None else filename
        return cls(
            version_id=version["id"],
            file_path=Path(version["archive_path"]),
            filename=filename,
            remote_path=join_backend_path(placement.get("folder_path") or "", remote_name),
        )


def needs_upload_check(job: dict[str, Any], backend_status: str | None) -> bool:
    if job["operation"] not in {Operation.create.value, Operation.update.value, Operation.move.value}:
        return False
    if job.get("job_status") != SyncJobStatus.failed.value:
        return False
    # 旧任务只有异常字符串。新任务由适配器使用明确的错误类型来标记。
    return backend_status == "unconfirmed" or bool(re.search(
        r"Weknora API error (?:5\d{2}|409)\b|timed out",
        job.get("job_error") or "",
        re.IGNORECASE,
    ))


class DocumentStatusReconciler:
    def __init__(
        self, store: SQLiteStore, is_sync_target_enabled: Callable[[int, str, str], bool],
    ) -> None:
        self.store = store
        self.is_sync_target_enabled = is_sync_target_enabled

    def record_upload_attempt(
        self, job: dict[str, Any], source: SyncUploadSource, fingerprint: DocumentUploadFingerprint,
    ) -> None:
        self.store.record_job_upload_attempt(job["id"], source.version_id, json.dumps({
            "remote_path": source.remote_path,
            "checksum": fingerprint.checksum,
            "size": fingerprint.size,
        }))

    def find_uploaded_document(
        self,
        adapter: DocumentReconciliationBackend,
        job: dict[str, Any],
        backend_kb_id: str,
        source: SyncUploadSource,
    ) -> MatchedBackendDocument | None:
        fingerprint = None
        try:
            if job.get("upload_attempt") and job["version_id"] == source.version_id:
                attempt = json.loads(job["upload_attempt"])
                if attempt["remote_path"] == source.remote_path:
                    fingerprint = DocumentUploadFingerprint(checksum=attempt["checksum"], size=attempt["size"])
            return adapter.find_uploaded_document(backend_kb_id, source.remote_path, source.file_path, fingerprint)
        except (DocumentStatusUncertain, RemoteKnowledgeBaseMissing):
            raise
        except Exception as exc:
            raise DocumentStatusUncertain("暂时无法向后端核对上传结果") from exc

    def save_remote_status(
        self,
        key: tuple[int, int, str],
        backend_doc_id: str,
        status: BackendDocStatus,
        version_id: int,
        *,
        recovered: bool = False,
    ) -> None:
        if recovered and self.store.backend_document_is_linked(*key, backend_doc_id):
            raise DocumentStatusUncertain("远端文档已关联到当前知识库中的另一份文件")
        self.store.upsert_sync_state(
            *key,
            backend_doc_id,
            SyncStateStatus.synced,
            backend_status=status.status,
            chunk_count=status.chunk_count,
            progress=status.progress,
            backend_error="后端报告文档解析失败" if status.status == "failed" else None,
            status_version_id=version_id,
        )
        self.store.mark_document_status_checked(*key)

    def check(
        self,
        adapters: dict[tuple[int, str], DocumentReconciliationBackend],
        *,
        include_failed: bool,
    ) -> None:
        started = time.monotonic()
        candidates = self.store.list_document_status_checks(list(adapters), include_failed=include_failed, limit=50)
        for candidate in candidates:
            if time.monotonic() - started >= 20:
                break
            key = (candidate["doc_id"], candidate["kb_id"], candidate["backend_slug"])
            self.store.mark_document_status_checked(*key)
            try:
                self._check_one(adapters[key[1:]], key, candidate)
            except Exception as exc:
                # 保留最后一次已确认的结果。按最近核对时间轮转，避免坏条目阻塞其他文档。
                logger.warning("文档状态核对未完成 doc=%s kb=%s backend=%s 原因=%s", *key, type(exc).__name__)

    def _check_one(
        self,
        adapter: DocumentReconciliationBackend,
        key: tuple[int, int, str],
        candidate: dict[str, Any],
    ) -> None:
        doc_id, kb_id, backend_slug = key
        version = self.store.get_current_document_version(doc_id)
        placement = self.store.get_document_placement(doc_id, kb_id)
        if version is None or placement is None:
            return
        job_id = candidate["id"]
        checked_version = candidate["status_version_id"] or candidate["version_id"]
        if checked_version is not None and checked_version != version["id"]:
            return
        remote_kb_id = candidate["backend_kb_id"]
        if not self.is_sync_target_enabled(kb_id, backend_slug, remote_kb_id):
            return
        recovering = candidate["job_status"] == SyncJobStatus.failed.value
        if recovering:
            source = SyncUploadSource.from_version(version, placement)
            match = self.find_uploaded_document(adapter, candidate, remote_kb_id, source)
            if match is None:
                return
        else:
            remote_id = candidate["backend_doc_id"]
            match = MatchedBackendDocument(remote_id, adapter.get_status(remote_kb_id, remote_id))

        with self.store.transaction():
            latest = self.store.get_latest_document_sync_job(*key)
            if (
                (latest and (latest["id"], latest["status"])) !=
                (None if job_id is None else (job_id, candidate["job_status"]))
                or not self.is_sync_target_enabled(kb_id, backend_slug, remote_kb_id)
                or self.store.get_current_document_version(doc_id) != version
                or self.store.get_document_placement(doc_id, kb_id) != placement
            ):
                return
            self.save_remote_status(key, match.id, match.status, version["id"], recovered=recovering)
            if recovering:
                self.store.upsert_backend_folder_mapping(
                    kb_id, backend_slug, placement["folder_id"],
                    placement.get("folder_path") or "", placement.get("folder_path") or "",
                    status="synced", error=None,
                )
                self.store.update_job_status(job_id, SyncJobStatus.succeeded)
                self.store.cancel_duplicate_document_uploads(*key, version["id"], job_id)
                logger.info("已核对远端上传 doc=%s kb=%s backend=%s job=%s 状态=%s", *key, job_id, match.status.status)
