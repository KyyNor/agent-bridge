"""工作流产物领域服务。

从 ``WorkflowService`` 抽出的产物 CRUD 与检索：保存产物、摄入解析结果、
按 id/条件查询、查看历史版本。门面通过薄转发维持对外签名兼容。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from agent_bridge.core.domain import AccessDenied, NotFound, ValidationError
from agent_bridge.automation.workflows.models import WorkflowArtifactFormat

if TYPE_CHECKING:
    from agent_bridge.storage.sqlite import SQLiteStore

logger = logging.getLogger(__name__)


def _snippet(content: str, query: str | None, size: int = 220) -> str:
    if not query:
        return content[:size]
    index = content.lower().find(query.lower())
    if index < 0:
        return content[:size]
    start = max(0, index - 60)
    end = min(len(content), index + size - 60)
    return content[start:end]


class ArtifactService:
    """工作流产物的写入、读取与检索。"""

    def __init__(
        self,
        *,
        store: "SQLiteStore",
        admins: set[str],
        workflow_service: Any = None,
    ) -> None:
        self.store = store
        self.admins = admins
        # 反向引用 WorkflowService，用于校验运行上下文等编排能力。可选以便
        # 在隔离测试中单独使用本服务。
        self.workflow_service = workflow_service

    def save_artifact(
        self,
        *,
        workflow_key: str,
        profile_key: str,
        run_id: str,
        task_key: str | None,
        title: str,
        path: str,
        tags: list[str],
        format: str,
        summary: str,
        content: str,
        metadata: dict[str, Any],
        task_version: str = "",
        producer_node_id: str | None = None,
        producer_node_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        workflow = self.store.get_workflow_definition(workflow_key)
        if workflow is None:
            raise NotFound("workflow not found")
        if workflow["profile_key"] != profile_key:
            raise ValidationError("workflow profile mismatch")
        try:
            artifact_format = WorkflowArtifactFormat(format).value
        except ValueError as exc:
            raise ValidationError("unsupported artifact format") from exc
        if not path or path.startswith("/") or ".." in path.split("/"):
            raise ValidationError("invalid artifact path")
        artifact_metadata = dict(metadata)
        if producer_node_id is not None:
            artifact_metadata.setdefault("producer_node_id", producer_node_id)
        if producer_node_fingerprint is not None:
            artifact_metadata.setdefault("producer_node_fingerprint", producer_node_fingerprint)
        saved = self.store.upsert_workflow_artifact(
            workflow_key=workflow_key,
            profile_key=profile_key,
            run_id=run_id,
            task_key=task_key,
            task_version=task_version,
            title=title,
            path=path,
            tags=tags,
            format=artifact_format,
            summary=summary,
            content=content,
            metadata=artifact_metadata,
            producer_node_id=producer_node_id,
            producer_node_fingerprint=producer_node_fingerprint,
        )
        if producer_node_id is not None:
            self.store.associate_workflow_run_artifacts(
                run_id,
                producer_node_id,
                [saved["artifact_id"]],
            )
        logger.info(
            "workflow 产物已保存 run_id=%s path=%s workflow=%s task=%s",
            run_id,
            path,
            workflow_key,
            task_key,
        )
        return saved

    def ingest_parsed_result(
        self,
        *,
        workflow_key: str,
        profile_key: str,
        run_id: str,
        parsed: Any,
    ) -> dict[str, Any]:
        self.workflow_service.require_workflow_run_context(
            profile_key=profile_key, workflow_key=workflow_key, run_id=run_id
        )
        if parsed.status == "no_executable_task":
            return {"status": "no_task", "artifact_count": 0}
        if parsed.status != "completed" or not parsed.task_key:
            raise ValidationError("parsed workflow result is not ingestible")
        saved = [
            self.save_artifact(
                workflow_key=workflow_key,
                profile_key=profile_key,
                run_id=run_id,
                task_key=parsed.task_key,
                task_version=parsed.task_version,
                title=artifact.title,
                path=artifact.path,
                tags=artifact.tags,
                format=artifact.format,
                summary=artifact.summary,
                content=artifact.content,
                metadata=artifact.metadata,
            )
            for artifact in parsed.artifacts
        ]
        completed = self.store.complete_workflow_task(
            workflow_key,
            parsed.task_key,
            task_version=parsed.task_version,
            run_id=run_id,
        )
        if not completed:
            raise ValidationError("workflow task lease mismatch")
        return {"status": "completed", "artifact_count": len(saved), "artifacts": saved}

    def get_artifact(
        self,
        *,
        actor: str,
        artifact_id: str,
        profile_key: str | None = None,
        trusted_profile_context: bool = False,
    ) -> dict[str, Any]:
        item = self.store.get_workflow_artifact(artifact_id)
        if item is None:
            raise NotFound("workflow artifact not found")
        if actor not in self.admins:
            if not profile_key:
                raise AccessDenied("capability profile is required")
            if not trusted_profile_context:
                raise AccessDenied("profile context is not trusted")
            if item["profile_key"] != profile_key:
                raise NotFound("workflow artifact not found")
        return {
            "artifact_id": item["artifact_id"],
            "workflow_key": item["workflow_key"],
            "profile_key": item["profile_key"],
            "run_id": item["run_id"],
            "task_key": item["task_key"],
            "task_version": item["task_version"],
            "is_current": item["is_current"],
            "title": item["title"],
            "path": item["path"],
            "tags": item["tags"],
            "format": item["format"],
            "summary": item["summary"],
            "content": item["content"],
            "metadata": item["metadata"],
            "created_at": item["created_at"],
            "updated_at": item["updated_at"],
        }

    def search_artifacts(
        self,
        *,
        actor: str,
        profile_key: str | None,
        query: str | None,
        tags: list[str],
        path: str | None,
        workflow_key: str | None,
        limit: int,
        offset: int = 0,
        task_key: str | None = None,
        task_version: str | None = None,
        run_id: str | None = None,
        include_history: bool = False,
        trusted_profile_context: bool = False,
        full: bool = False,
        format: str | None = None,
        paginated: bool = False,
        path_match: str | None = None,
    ) -> dict[str, Any]:
        if actor not in self.admins and not profile_key:
            raise AccessDenied("capability profile is required")
        if actor not in self.admins and profile_key and not trusted_profile_context:
            raise AccessDenied("profile context is not trusted")
        if profile_key:
            profile = self.store.get_project_profile(profile_key)
            if profile is None:
                raise NotFound("profile not found")
            if profile.get("status") != "active":
                raise ValidationError("profile is disabled")
        if limit < 1:
            raise ValidationError("limit must be positive")
        bounded_limit = min(limit, 50)
        bounded_offset = max(offset, 0)
        if paginated:
            page = self.store.workflows.search_workflow_artifacts_page(
                profile_key=profile_key,
                query=query,
                tags=tags,
                path=path,
                workflow_key=workflow_key,
                task_key=task_key,
                task_version=task_version,
                run_id=run_id,
                include_history=include_history,
                limit=bounded_limit,
                offset=bounded_offset,
                format=format,
                path_match=path_match,
            )
            items = page["items"]
        else:
            items = self.store.search_workflow_artifacts(
                profile_key=profile_key,
                query=query,
                tags=tags,
                path=path,
                workflow_key=workflow_key,
                task_key=task_key,
                task_version=task_version,
                run_id=run_id,
                include_history=include_history,
                limit=bounded_limit,
                format=format,
                path_match=path_match,
            )
        def _entry(item: dict[str, Any]) -> dict[str, Any]:
            entry = {
                "artifact_id": item["artifact_id"],
                "workflow_key": item["workflow_key"],
                "profile_key": item["profile_key"],
                "run_id": item["run_id"],
                "task_key": item["task_key"],
                "task_version": item["task_version"],
                "is_current": item["is_current"],
                "title": item["title"],
                "path": item["path"],
                "tags": item["tags"],
                "format": item["format"],
                "summary": item["summary"],
                "snippet": _snippet(item["content"], query),
                "created_at": item["created_at"],
                "updated_at": item["updated_at"],
            }
            # Return the full body when explicitly requested (feature: view a
            # task's outputs from the progress page) or on an exact-path lookup
            # ("fetch this one"). Prefix matches keep snippet-only otherwise.
            if full or (path and item["path"] == path):
                entry["content"] = item["content"]
            return entry

        result = {"items": [_entry(item) for item in items]}
        if paginated:
            result.update(
                {
                    "total": page["total"],
                    "limit": page["limit"],
                    "offset": page["offset"],
                }
            )
        return result

    def list_artifact_history(
        self,
        *,
        actor: str,
        profile_key: str | None,
        workflow_key: str,
        task_key: str,
        limit: int,
        trusted_profile_context: bool = False,
    ) -> dict[str, Any]:
        if actor not in self.admins and not profile_key:
            raise AccessDenied("capability profile is required")
        if actor not in self.admins and profile_key and not trusted_profile_context:
            raise AccessDenied("profile context is not trusted")
        if not workflow_key:
            raise ValidationError("workflow_key is required")
        if not task_key:
            raise ValidationError("task_key is required")
        if limit < 1:
            raise ValidationError("limit must be positive")
        bounded_limit = min(limit, 50)
        items = self.store.search_workflow_artifacts(
            profile_key=profile_key,
            query=None,
            tags=[],
            path=None,
            workflow_key=workflow_key,
            task_key=task_key,
            task_version=None,
            include_history=True,
            format="all",
            limit=200,
        )

        # 两层分组：外层按 task_version（保留版本历史语义），内层按 run_id
        # （展示同一 task_version 的多次执行）。这样同 task_version 多 run 时，
        # 每个 run 都能展开查看，而不是被折叠成单个 current。
        versions: list[dict[str, Any]] = []
        by_version: dict[str, dict[str, Any]] = {}
        for item in items:
            version = item["task_version"]
            entry = by_version.get(version)
            if entry is None:
                entry = {
                    "workflow_key": item["workflow_key"],
                    "task_key": item["task_key"],
                    "task_version": version,
                    "is_current": False,
                    "updated_at": item["updated_at"],
                    "runs": [],
                    "_by_run": {},
                }
                by_version[version] = entry
                versions.append(entry)
            entry["is_current"] = bool(entry["is_current"] or item["is_current"])
            if item["updated_at"] > entry["updated_at"]:
                entry["updated_at"] = item["updated_at"]
            run_entry = entry["_by_run"].get(item["run_id"])
            if run_entry is None:
                run_entry = {
                    "run_id": item["run_id"],
                    "is_current": bool(item["is_current"]),
                    "updated_at": item["updated_at"],
                    "artifacts": [],
                }
                entry["_by_run"][item["run_id"]] = run_entry
                entry["runs"].append(run_entry)
            else:
                run_entry["is_current"] = bool(
                    run_entry["is_current"] or item["is_current"]
                )
                if item["updated_at"] > run_entry["updated_at"]:
                    run_entry["updated_at"] = item["updated_at"]
            run_entry["artifacts"].append(
                {
                    "artifact_id": item["artifact_id"],
                    "run_id": item["run_id"],
                    "title": item["title"],
                    "path": item["path"],
                    "tags": item["tags"],
                    "format": item["format"],
                    "summary": item["summary"],
                    "content": item["content"],
                    "created_at": item["created_at"],
                    "updated_at": item["updated_at"],
                }
            )

        # 版本桶按最新更新时间倒序，run 桶按更新时间倒序；移除内部索引字段。
        for entry in versions:
            entry["runs"].sort(key=lambda run: run["updated_at"], reverse=True)
            entry.pop("_by_run", None)
        versions.sort(key=lambda entry: entry["updated_at"], reverse=True)

        return {"versions": versions[:bounded_limit]}
