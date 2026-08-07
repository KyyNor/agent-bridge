"""技能提示词与 CodeGraph 的兼容方法。"""

from __future__ import annotations

from typing import Any

from agent_bridge.storage.repositories import revisions as _revisions


class CoreFacadeMixin:
    def create_model_evaluation_run(self, **kwargs: Any) -> dict[str, Any]:
        return self.model_evaluations.create_run(**kwargs)

    def get_model_evaluation_run(self, run_id: str) -> dict[str, Any] | None:
        return self.model_evaluations.get_run(run_id)

    def list_model_evaluation_runs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self.model_evaluations.list_runs(limit=limit)

    def update_model_evaluation_run(self, run_id: str, **kwargs: Any) -> dict[str, Any] | None:
        return self.model_evaluations.update_run(run_id, **kwargs)

    def abandon_model_evaluation_runs(self) -> int:
        return self.model_evaluations.abandon_active_runs()

    def create_model_evaluation_execution(self, **kwargs: Any) -> dict[str, Any]:
        return self.model_evaluations.create_execution(**kwargs)

    def get_model_evaluation_execution(self, execution_id: str) -> dict[str, Any] | None:
        return self.model_evaluations.get_execution(execution_id)

    def list_model_evaluation_executions(self, run_id: str) -> list[dict[str, Any]]:
        return self.model_evaluations.list_executions(run_id)

    def update_model_evaluation_execution(self, execution_id: str, **kwargs: Any) -> dict[str, Any] | None:
        return self.model_evaluations.update_execution(execution_id, **kwargs)

    def get_retrieval_probe_llm_config(self) -> dict[str, Any]:
        return self.retrieval_probe_config.get_llm_config()

    def save_retrieval_probe_llm_config(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None,
        clear_api_key: bool,
    ) -> dict[str, Any]:
        return self.retrieval_probe_config.save_llm_config(
            base_url=base_url,
            model=model,
            api_key=api_key,
            clear_api_key=clear_api_key,
        )

    def get_skill_prompt_override(self, skill_name: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM skill_prompts WHERE skill_name = ?", (skill_name,)).fetchone()
            return dict(row) if row is not None else None

    def list_skill_prompt_overrides(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM skill_prompts ORDER BY skill_name").fetchall()
            return [dict(row) for row in rows]

    def upsert_skill_prompt_override(self, *, skill_name: str, prompt: str, updated_by: str) -> dict[str, Any]:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO skill_prompts (skill_name, prompt, updated_by)
                VALUES (?, ?, ?)
                ON CONFLICT(skill_name) DO UPDATE SET
                  prompt = excluded.prompt,
                  updated_by = excluded.updated_by,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (skill_name, prompt, updated_by),
            )
            row = conn.execute("SELECT * FROM skill_prompts WHERE skill_name = ?", (skill_name,)).fetchone()
            if row is None:
                raise KeyError(f"skill not found: {skill_name}")
            return dict(row)

    def delete_skill_prompt_override(self, skill_name: str) -> bool:
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM skill_prompts WHERE skill_name = ?", (skill_name,))
            return cursor.rowcount > 0

    # --- skill prompt revisions ------------------------------------------

    def create_skill_prompt_revision(
        self, *, skill_name: str, content_hash: str, snapshot: dict[str, Any], actor: str
    ) -> dict[str, Any]:
        with self.connect() as conn:
            # current_revision_no lives on skill_prompts; reset (delete override) means
            # the row is gone, so the UPDATE inside create_revision bumps 0 rows in
            # that case — matching the previous conditional behavior.
            return _revisions.create_revision(
                conn,
                table="skill_prompt_revisions",
                key_column="skill_name",
                key_value=skill_name,
                content_hash=content_hash,
                snapshot=snapshot,
                actor=actor,
                owner_table="skill_prompts",
                snapshot_label="skill",
            )

    def list_skill_prompt_revisions(self, skill_name: str, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return _revisions.list_revisions(
                conn,
                table="skill_prompt_revisions",
                key_column="skill_name",
                key_value=skill_name,
                limit=limit,
            )

    def get_skill_prompt_revision(self, skill_name: str, revision_no: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            return _revisions.get_revision(
                conn,
                table="skill_prompt_revisions",
                key_column="skill_name",
                key_value=skill_name,
                revision_no=revision_no,
                snapshot_label="skill",
            )

    def upsert_code_repository(
        self,
        *,
        repo_key: str,
        name: str,
        git_url: str,
        branch: str,
        auth_ref: str,
        description: str,
        tags: list[str],
        category_key: str,
        sync_interval_minutes: int,
        auto_understand: bool,
        status: str,
    ) -> dict[str, Any]:
        return self.codegraph.upsert_code_repository(repo_key=repo_key, name=name, git_url=git_url, branch=branch, auth_ref=auth_ref, description=description, tags=tags, category_key=category_key, sync_interval_minutes=sync_interval_minutes, auto_understand=auto_understand, status=status)

    def list_code_repositories(self) -> list[dict[str, Any]]:
        return self.codegraph.list_code_repositories()

    def get_code_repository(self, repo_key: str) -> dict[str, Any] | None:
        return self.codegraph.get_code_repository(repo_key=repo_key)

    def mark_code_repository_sync(
        self,
        repo_key: str,
        *,
        local_path: str,
        last_commit: str | None,
        success: bool,
        error: str | None,
    ) -> None:
        return self.codegraph.mark_code_repository_sync(repo_key=repo_key, local_path=local_path, last_commit=last_commit, success=success, error=error)

    def create_codegraph_sync_run(self, repo_key: str, *, status: str, stage: str) -> dict[str, Any]:
        return self.codegraph.create_codegraph_sync_run(repo_key=repo_key, status=status, stage=stage)

    def update_codegraph_sync_run(self, run_id: int, *, stage: str) -> None:
        return self.codegraph.update_codegraph_sync_run(run_id=run_id, stage=stage)

    def finish_codegraph_sync_run(
        self,
        run_id: int,
        *,
        status: str,
        stage: str,
        error: str | None,
        duration_ms: int | None,
    ) -> dict[str, Any]:
        return self.codegraph.finish_codegraph_sync_run(run_id=run_id, status=status, stage=stage, error=error, duration_ms=duration_ms)

    def interrupt_running_codegraph_sync_runs(self, *, error: str) -> int:
        return self.codegraph.interrupt_running_codegraph_sync_runs(error=error)

    # -- Categories --

    def upsert_category(self, *, category_key: str, name: str, description: str) -> dict[str, Any]:
        return self.codegraph.upsert_category(category_key=category_key, name=name, description=description)

    def list_categories(self) -> list[dict[str, Any]]:
        return self.codegraph.list_categories()

    def delete_category(self, category_key: str) -> None:
        return self.codegraph.delete_category(category_key=category_key)

    def delete_code_repository(self, repo_key: str) -> None:
        return self.codegraph.delete_repository(repo_key=repo_key)

    # -- Sync Config --

    def get_sync_config(self) -> dict[str, Any]:
        return self.codegraph.get_sync_config()

    def save_sync_config(
        self,
        *,
        code_sync_cron: str,
        ua_git_url: str = "",
        ua_plugin_update_cron: str = "0 3 * * 0",
        claude_mem_git_url: str = "",
        claude_mem_plugin_update_cron: str = "30 3 * * 0",
        understand_cron: str = "0 2 * * *",
        doc_sync_cron: str = "*/30 * * * *",
        workflow_start_time: str = "22:00",
        workflow_stop_time: str = "07:00",
        workflow_max_runs: int = 0,
        workflow_max_concurrent_runs: int = 4,
        workflow_max_concurrent_runs_per_workflow: int = 2,
        workflow_max_runtime_minutes: int = 30,
        workflow_task_rerun_days: int = 30,
        log_retention_days: int = 180,
        mcp_timeout_seconds: int = 150,
        understand_timeout_minutes: int = 120,
        artifact_search_cache_ttl_hours: int = 8,
    ) -> dict[str, Any]:
        return self.codegraph.save_sync_config(
            code_sync_cron=code_sync_cron,
            ua_git_url=ua_git_url,
            ua_plugin_update_cron=ua_plugin_update_cron,
            claude_mem_git_url=claude_mem_git_url,
            claude_mem_plugin_update_cron=claude_mem_plugin_update_cron,
            understand_cron=understand_cron,
            doc_sync_cron=doc_sync_cron,
            workflow_start_time=workflow_start_time,
            workflow_stop_time=workflow_stop_time,
            workflow_max_runs=workflow_max_runs,
            workflow_max_concurrent_runs=workflow_max_concurrent_runs,
            workflow_max_concurrent_runs_per_workflow=workflow_max_concurrent_runs_per_workflow,
            workflow_max_runtime_minutes=workflow_max_runtime_minutes,
            workflow_task_rerun_days=workflow_task_rerun_days,
            log_retention_days=log_retention_days,
            mcp_timeout_seconds=mcp_timeout_seconds,
            understand_timeout_minutes=understand_timeout_minutes,
            artifact_search_cache_ttl_hours=artifact_search_cache_ttl_hours,
        )
