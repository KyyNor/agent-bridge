"""统一数据生命周期治理 v1。

所有可清理历史数据由一个每日定时任务处理（默认 22:00），替代旧的
「写入路径上最多每小时一次」的 runtime log prune：

- 详情窗口（``retention_detail_days``，默认 20 天）内保留完整数据；
- 详情窗口之外、历史窗口（``retention_history_days``，默认 60 天）内清理
  大字段、保留轻量元数据（状态、耗时、错误、摘要等）；
- 超过历史窗口的记录删除，关联数据（工作流节点/运行产物关联、评测执行）
  随外键级联，FTS 由触发器同步；
- Agent 运行目录与模型评测 work_dir 按详情/历史窗口清理磁盘文件；
- 日常任务只做分批 DELETE + ``wal_checkpoint``，不 VACUUM（写入直接复用
  freelist）；首次升级到本版本时按 ``2026_09_data_retention_v1`` migration
  marker 执行一次历史清理 + VACUUM，分库记录阶段 marker，中断后幂等恢复。

明确的不可清理项：业务台账（ledgers 库）、知识库/文档/版本、用户/组/权限/
Profile、Script/Skill/Workflow 定义与 revision、``is_current = 1`` 的当前
工作流产物、业务与 DSH/Memory 配置、``workflow_tasks``（第一版不纳入）。
"""

from __future__ import annotations

import logging
import re
import shutil
import sqlite3
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

from agent_bridge.core.domain import ValidationError
from agent_bridge.core.timeutil import utc_now

logger = logging.getLogger(__name__)

DEFAULT_DETAIL_DAYS = 20
DEFAULT_HISTORY_DAYS = 60
DEFAULT_CLEANUP_TIME = "22:00"

# 每批 DELETE / UPDATE 的行数上限：避免长事务与 WAL 暴涨。
DELETE_BATCH_ROWS = 500

# 首次升级 migration marker（issue #12 建议）：分阶段记录，中断后跳过已完成阶段。
RETENTION_V1_CLEANUP_MARKER = "retention_v1.cleanup"
RETENTION_V1_MAIN_VACUUM_MARKER = "retention_v1.main_vacuum"
RETENTION_V1_LOGS_VACUUM_MARKER = "retention_v1.logs_vacuum"
RETENTION_V1_LEDGER_VACUUM_MARKER = "retention_v1.ledger_vacuum"

# 台账库仅当 freelist 明显偏高时才 VACUUM（页数 > 10% 且超过 512 页）。
LEDGER_VACUUM_MIN_FREELIST_PAGES = 512
LEDGER_VACUUM_FREELIST_RATIO = 0.1

_CLEANUP_TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

DATA_RETENTION_META_SCHEMA = """
CREATE TABLE IF NOT EXISTS data_retention_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def normalize_detail_days(value: Any) -> int:
    try:
        days = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("详情保留天数必须是正整数") from exc
    if days < 1:
        raise ValidationError("详情保留天数必须 >= 1")
    return days


def normalize_history_days(value: Any) -> int:
    try:
        days = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("历史保留天数必须是正整数") from exc
    if days < 1:
        raise ValidationError("历史保留天数必须 >= 1")
    return days


def normalize_cleanup_time(value: Any) -> str:
    cleaned = str(value or "").strip()
    if not _CLEANUP_TIME_PATTERN.fullmatch(cleaned):
        raise ValidationError("每日清理时间必须是 HH:MM（24 小时制）")
    return cleaned


def resolve_retention_config(sync_config: dict[str, Any]) -> dict[str, Any]:
    """把 ``knowledge_sync_config`` 行解析为生命周期配置，缺省回落默认值。"""
    detail = normalize_detail_days(
        sync_config.get("retention_detail_days") or DEFAULT_DETAIL_DAYS
    )
    history = normalize_history_days(
        sync_config.get("retention_history_days") or DEFAULT_HISTORY_DAYS
    )
    if detail > history:
        raise ValidationError(
            f"详情保留天数（{detail}）不能大于历史保留天数（{history}）"
        )
    cleanup_time = normalize_cleanup_time(
        sync_config.get("retention_cleanup_time") or DEFAULT_CLEANUP_TIME
    )
    return {
        "detail_days": detail,
        "history_days": history,
        "cleanup_time": cleanup_time,
    }


def ensure_data_retention_meta(conn: sqlite3.Connection) -> None:
    conn.executescript(DATA_RETENTION_META_SCHEMA)


def _cutoff_stamp(days: int) -> str:
    """删除/瘦身边界，统一 ``YYYY-MM-DD HH:MM:SS``（比较时经 datetime() 归一）。"""
    return (utc_now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def _db_disk_size(db_path: Path) -> int:
    """数据库占用的磁盘总量（含 WAL 预写日志），供清理前后对比。"""
    total = 0
    for suffix in ("", "-wal", "-shm"):
        try:
            total += db_path.with_name(db_path.name + suffix).stat().st_size
        except OSError:
            continue
    return total


def _format_size(size_bytes: int) -> str:
    value = float(size_bytes)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024.0 or unit == "GiB":
            return f"{value:.1f}{unit}"
        value /= 1024.0
    return f"{value:.1f}GiB"


class DataRetentionService:
    """按 detail/history 双窗口清理历史数据与运行目录。"""

    def __init__(self, *, store, paths, ledger_db_path: Path | None = None) -> None:
        self._store = store
        self._paths = paths
        self._ledger_db_path = ledger_db_path
        # 串行化清理执行：定时任务 / 保存配置 / 首次升级迁移不并发进入。
        self._run_lock = threading.Lock()

    # -- 对外入口 --

    def retention_config(self) -> dict[str, Any]:
        return resolve_retention_config(self._store.get_sync_config())

    def run_daily_cleanup(self) -> dict[str, Any]:
        """执行一轮完整生命周期清理（日常路径：只 DELETE + checkpoint，不 VACUUM）。"""
        if not self._run_lock.acquire(blocking=False):
            logger.warning("数据生命周期清理已在执行中，本次触发跳过")
            return {"skipped": "already_running"}
        try:
            started = time.monotonic()
            config = self.retention_config()
            detail_days = config["detail_days"]
            history_days = config["history_days"]
            detail_cutoff = _cutoff_stamp(detail_days)
            history_cutoff = _cutoff_stamp(history_days)
            logger.info(
                "数据生命周期清理开始 detail_days=%s history_days=%s",
                detail_days,
                history_days,
            )
            logs_summary = self._cleanup_logs_db(detail_cutoff, history_cutoff)
            main_summary = self._cleanup_main_db(detail_cutoff, history_cutoff)
            dirs_summary = self._cleanup_disk_dirs(detail_days, history_days)
            checkpoint = self._checkpoint_databases()
            summary = {
                "config": config,
                "logs_db": logs_summary,
                "main_db": main_summary,
                "dirs": dirs_summary,
                "checkpoint": checkpoint,
                "duration_seconds": round(time.monotonic() - started, 3),
            }
            logger.info(
                "数据生命周期清理完成 耗时=%.1fs 删除=%s 瘦身=%s 目录=%s checkpoint=%s",
                summary["duration_seconds"],
                {**logs_summary["deleted"], **main_summary["deleted"]},
                {**logs_summary["slimmed"], **main_summary["slimmed"]},
                dirs_summary,
                checkpoint,
            )
            return summary
        finally:
            self._run_lock.release()

    def run_first_upgrade(self) -> dict[str, Any]:
        """首次升级迁移：历史清理 + 一次性 VACUUM，按阶段 marker 幂等恢复。"""
        summary: dict[str, Any] = {"stages": {}}
        separate_log_db = self._store.log_db_path != self._store.db_path
        main_before = _db_disk_size(self._store.db_path)
        logs_before = _db_disk_size(self._store.log_db_path)
        if not self._marker(RETENTION_V1_CLEANUP_MARKER):
            # 兼容旧 log_retention_days：管理员显式设置过更短/更长的保留期时，
            # 迁移为对应 history_days，避免新默认悄悄改变已有保留语义。
            self._migrate_legacy_log_retention()
            started = time.monotonic()
            cleanup = self.run_daily_cleanup()
            self._set_marker(RETENTION_V1_CLEANUP_MARKER, "done")
            summary["stages"]["cleanup"] = {
                "duration_seconds": cleanup.get("duration_seconds"),
            }
            logger.info(
                "数据生命周期首次升级：历史清理完成 耗时=%.1fs",
                time.monotonic() - started,
            )
        else:
            summary["stages"]["cleanup"] = {"skipped": "marker_done"}

        if not self._marker(RETENTION_V1_MAIN_VACUUM_MARKER):
            duration = self._vacuum_database(self._store.db_path, label="主库 agent-bridge.db")
            self._set_marker(RETENTION_V1_MAIN_VACUUM_MARKER, "done")
            summary["stages"]["main_vacuum"] = {"duration_seconds": duration}
        else:
            summary["stages"]["main_vacuum"] = {"skipped": "marker_done"}

        if separate_log_db and not self._marker(RETENTION_V1_LOGS_VACUUM_MARKER):
            duration = self._vacuum_database(
                self._store.log_db_path, label="日志库 agent-bridge-logs.db"
            )
            self._set_marker(RETENTION_V1_LOGS_VACUUM_MARKER, "done")
            summary["stages"]["logs_vacuum"] = {"duration_seconds": duration}
        else:
            summary["stages"]["logs_vacuum"] = {"skipped": "marker_done"}

        if not self._marker(RETENTION_V1_LEDGER_VACUUM_MARKER):
            outcome = self._vacuum_ledger_database()
            self._set_marker(RETENTION_V1_LEDGER_VACUUM_MARKER, outcome)
            summary["stages"]["ledger_vacuum"] = {"outcome": outcome}
        else:
            summary["stages"]["ledger_vacuum"] = {"skipped": "marker_done"}

        main_after = _db_disk_size(self._store.db_path)
        logs_after = _db_disk_size(self._store.log_db_path)
        summary["sizes"] = {
            "main_db_before": main_before,
            "main_db_after": main_after,
            "logs_db_before": logs_before,
            "logs_db_after": logs_after,
        }
        logger.info(
            "数据生命周期首次升级迁移完成 主库=%s→%s 日志库=%s→%s",
            _format_size(main_before),
            _format_size(main_after),
            _format_size(logs_before),
            _format_size(logs_after),
        )
        return summary

    # -- 日志库（agent-bridge-logs.db）清理 --

    def _cleanup_logs_db(self, detail_cutoff: str, history_cutoff: str) -> dict[str, Any]:
        deleted: dict[str, int] = {}
        slimmed: dict[str, int] = {}

        deleted["agent_runs"] = self._batched_statement(
            self._store.log_connect,
            """
            DELETE FROM agent_runs WHERE rowid IN (
              SELECT rowid FROM agent_runs
              WHERE datetime(created_at) < datetime(?) LIMIT ?
            )
            """,
            (history_cutoff,),
        )
        slimmed["agent_runs"] = self._batched_statement(
            self._store.log_connect,
            """
            UPDATE agent_runs SET prompt = '', events_json = '[]',
                   result_json = NULL, output_schema_json = NULL
            WHERE rowid IN (
              SELECT rowid FROM agent_runs
              WHERE datetime(created_at) < datetime(?)
                AND datetime(created_at) >= datetime(?)
                AND (prompt != '' OR events_json != '[]'
                     OR result_json IS NOT NULL OR output_schema_json IS NOT NULL)
              LIMIT ?
            )
            """,
            (detail_cutoff, history_cutoff),
        )
        deleted["tool_call_logs"] = self._batched_statement(
            self._store.log_connect,
            """
            DELETE FROM tool_call_logs WHERE rowid IN (
              SELECT rowid FROM tool_call_logs
              WHERE datetime(created_at) < datetime(?) LIMIT ?
            )
            """,
            (history_cutoff,),
        )
        # 请求/响应原文清理后仍保留 request/response summary 等轻量审计字段。
        slimmed["tool_call_logs"] = self._batched_statement(
            self._store.log_connect,
            """
            UPDATE tool_call_logs SET request_json = '{}', response_json = '{}'
            WHERE rowid IN (
              SELECT rowid FROM tool_call_logs
              WHERE datetime(created_at) < datetime(?)
                AND datetime(created_at) >= datetime(?)
                AND (request_json != '{}' OR response_json != '{}')
              LIMIT ?
            )
            """,
            (detail_cutoff, history_cutoff),
        )
        return {"deleted": deleted, "slimmed": slimmed}

    # -- 主库（agent-bridge.db）清理 --

    def _cleanup_main_db(self, detail_cutoff: str, history_cutoff: str) -> dict[str, Any]:
        deleted: dict[str, int] = {}
        slimmed: dict[str, int] = {}

        # P0：workflow_run_logs 超过详情窗口直接删除。
        deleted["workflow_run_logs"] = self._batched_statement(
            self._store.connect,
            """
            DELETE FROM workflow_run_logs WHERE rowid IN (
              SELECT rowid FROM workflow_run_logs
              WHERE datetime(created_at) < datetime(?) LIMIT ?
            )
            """,
            (detail_cutoff,),
        )

        slimmed["script_runs"] = self._batched_statement(
            self._store.connect,
            """
            UPDATE script_runs SET stdout = '', stderr = '', result_json = '{}'
            WHERE rowid IN (
              SELECT rowid FROM script_runs
              WHERE datetime(created_at) < datetime(?)
                AND datetime(created_at) >= datetime(?)
                AND (stdout != '' OR stderr != '' OR result_json != '{}')
              LIMIT ?
            )
            """,
            (detail_cutoff, history_cutoff),
        )
        deleted["script_runs"] = self._batched_statement(
            self._store.connect,
            """
            DELETE FROM script_runs WHERE rowid IN (
              SELECT rowid FROM script_runs
              WHERE datetime(created_at) < datetime(?) LIMIT ?
            )
            """,
            (history_cutoff,),
        )

        # P1：workflow_runs 删除；节点运行与 run-artifact 关联随外键级联，
        # 行删除后同步回收 temp_dir 磁盘目录。级联行数以删除前的从属行数为准。
        cascade_counts = self._count_workflow_run_cascades(history_cutoff)
        temp_dirs = self._collect_workflow_temp_dirs(history_cutoff)
        deleted["workflow_runs"] = self._batched_statement(
            self._store.connect,
            """
            DELETE FROM workflow_runs WHERE rowid IN (
              SELECT rowid FROM workflow_runs
              WHERE datetime(COALESCE(finished_at, started_at)) < datetime(?) LIMIT ?
            )
            """,
            (history_cutoff,),
        )
        deleted["workflow_node_runs"] = cascade_counts["workflow_node_runs"]
        deleted["workflow_run_artifacts"] = cascade_counts["workflow_run_artifacts"]
        removed_temp_dirs = self._remove_workflow_temp_dirs(temp_dirs)
        if removed_temp_dirs:
            logger.info(
                "工作流运行目录已清理 count=%d root=%s",
                removed_temp_dirs,
                self._workflow_run_root(),
            )

        # P1：历史产物（is_current = 0）；当前产物永久保留。FTS 内容表与
        # 虚拟表由 workflow_artifacts 上的 DELETE 触发器同步清理。
        deleted["workflow_artifacts"] = self._batched_statement(
            self._store.connect,
            """
            DELETE FROM workflow_artifacts WHERE rowid IN (
              SELECT rowid FROM workflow_artifacts
              WHERE is_current = 0 AND datetime(updated_at) < datetime(?) LIMIT ?
            )
            """,
            (history_cutoff,),
        )

        # P1：模型评测运行（执行表外键级联）+ work_dir 磁盘回收。
        eval_work_dirs = self._collect_model_evaluation_work_dirs(history_cutoff)
        with self._store.connect() as conn:
            deleted["model_evaluation_executions"] = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM model_evaluation_executions
                    WHERE run_id IN (
                      SELECT run_id FROM model_evaluation_runs
                      WHERE datetime(created_at) < datetime(?)
                    )
                    """,
                    (history_cutoff,),
                ).fetchone()[0]
            )
        deleted["model_evaluation_runs"] = self._batched_statement(
            self._store.connect,
            """
            DELETE FROM model_evaluation_runs WHERE rowid IN (
              SELECT rowid FROM model_evaluation_runs
              WHERE datetime(created_at) < datetime(?) LIMIT ?
            )
            """,
            (history_cutoff,),
        )
        removed_eval_dirs = self._remove_dirs(eval_work_dirs)
        if removed_eval_dirs:
            logger.info("模型评测 work_dir 已清理 count=%d", removed_eval_dirs)

        # P2：低风险历史/临时数据。
        deleted["sync_jobs"] = self._batched_statement(
            self._store.connect,
            """
            DELETE FROM sync_jobs WHERE rowid IN (
              SELECT rowid FROM sync_jobs
              WHERE datetime(COALESCE(updated_at, created_at)) < datetime(?) LIMIT ?
            )
            """,
            (history_cutoff,),
        )
        deleted["codegraph_sync_runs"] = self._batched_statement(
            self._store.connect,
            """
            DELETE FROM codegraph_sync_runs WHERE rowid IN (
              SELECT rowid FROM codegraph_sync_runs
              WHERE datetime(COALESCE(finished_at, started_at)) < datetime(?) LIMIT ?
            )
            """,
            (history_cutoff,),
        )
        deleted["workflow_task_imports"] = self._delete_expired_imports(
            "workflow_task_imports"
        )
        deleted["workflow_definition_imports"] = self._delete_expired_imports(
            "workflow_definition_imports"
        )
        return {"deleted": deleted, "slimmed": slimmed}

    # -- 磁盘目录清理 --

    def _cleanup_disk_dirs(self, detail_days: int, history_days: int) -> dict[str, int]:
        summary: dict[str, int] = {
            "agent_run_dirs": 0,
            "workflow_run_dirs": 0,
            "model_evaluation_dirs": 0,
        }
        summary["agent_run_dirs"] = self._cleanup_agent_run_dirs(detail_days)
        # workflow/evaluation 目录随对应 DB 行删除（_cleanup_main_db 已处理），
        # 这里兜底回收行已不存在的孤儿目录。
        summary["workflow_run_dirs"] = self._cleanup_orphan_run_dirs(
            self._workflow_run_root(),
            self._existing_workflow_temp_dirs(),
        )
        eval_root = self._paths.run_dir / "model-evaluations"
        summary["model_evaluation_dirs"] = self._cleanup_orphan_run_dirs(
            eval_root,
            self._existing_model_evaluation_dirs(),
        )
        return summary

    def _cleanup_agent_run_dirs(self, detail_days: int) -> int:
        """回收超过详情窗口且已结束的 Agent 运行目录；运行中的目录绝不删除。"""
        base = self._paths.run_dir / "agent-runs"
        if not base.is_dir():
            return 0
        active_names: set[str] = set()
        with self._store.log_connect() as conn:
            for row in conn.execute(
                """
                SELECT cwd FROM agent_runs
                WHERE status = 'running' AND cwd IS NOT NULL AND cwd != ''
                """
            ).fetchall():
                active_names.add(Path(str(row["cwd"])).name)
        cutoff_epoch = (utc_now() - timedelta(days=detail_days)).timestamp()
        removed = 0
        for child in sorted(base.iterdir()):
            if child.name in active_names:
                continue
            try:
                mtime = child.stat().st_mtime
            except OSError:
                continue
            if mtime >= cutoff_epoch:
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                # managed run 产生的过程文件同样按窗口回收。
                try:
                    child.unlink(missing_ok=True)
                except OSError:
                    continue
            removed += 1
            logger.info("Agent 运行目录已清理 path=%s", child)
        return removed

    def _workflow_run_root(self) -> Path:
        return self._paths.run_dir / "workflow-runs"

    def _collect_workflow_temp_dirs(self, history_cutoff: str) -> list[str]:
        with self._store.connect() as conn:
            rows = conn.execute(
                """
                SELECT temp_dir FROM workflow_runs
                WHERE datetime(COALESCE(finished_at, started_at)) < datetime(?)
                  AND temp_dir IS NOT NULL AND temp_dir != ''
                """,
                (history_cutoff,),
            ).fetchall()
        return [str(row["temp_dir"]) for row in rows]

    def _remove_workflow_temp_dirs(self, temp_dirs: list[str]) -> int:
        root = self._workflow_run_root().resolve()
        candidates = []
        for item in temp_dirs:
            path = Path(item)
            try:
                resolved = path.resolve()
            except OSError:
                continue
            # 只回收工作流运行根目录内的目录，防止异常数据指向任意路径。
            if resolved == root or root not in resolved.parents:
                continue
            candidates.append(resolved)
        return self._remove_dirs(candidates)

    def _collect_model_evaluation_work_dirs(self, history_cutoff: str) -> list[str]:
        with self._store.connect() as conn:
            rows = conn.execute(
                """
                SELECT work_dir FROM model_evaluation_runs
                WHERE datetime(created_at) < datetime(?)
                  AND work_dir IS NOT NULL AND work_dir != ''
                """,
                (history_cutoff,),
            ).fetchall()
        return [str(row["work_dir"]) for row in rows]

    def _existing_workflow_temp_dirs(self) -> set[str]:
        with self._store.connect() as conn:
            rows = conn.execute(
                "SELECT temp_dir FROM workflow_runs WHERE temp_dir IS NOT NULL AND temp_dir != ''"
            ).fetchall()
        return {str(Path(str(row["temp_dir"])).resolve()) for row in rows}

    def _existing_model_evaluation_dirs(self) -> set[str]:
        with self._store.connect() as conn:
            rows = conn.execute(
                "SELECT work_dir FROM model_evaluation_runs WHERE work_dir IS NOT NULL AND work_dir != ''"
            ).fetchall()
        return {str(Path(str(row["work_dir"])).resolve()) for row in rows}

    def _cleanup_orphan_run_dirs(self, root: Path, referenced: set[str]) -> int:
        """回收 root 下已无 DB 行引用的孤儿目录（兜底，正常路径不产生）。"""
        if not root.is_dir():
            return 0
        removed = 0
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            try:
                resolved = str(child.resolve())
            except OSError:
                continue
            if resolved in referenced:
                continue
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
            logger.info("孤儿运行目录已清理 path=%s", child)
        return removed

    @staticmethod
    def _remove_dirs(dirs: list[str] | list[Path]) -> int:
        removed = 0
        for item in dirs:
            path = Path(item)
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
        return removed

    # -- 批量执行与计数 --

    def _batched_statement(
        self,
        connect,
        statement: str,
        params: tuple,
        *,
        batch_size: int = DELETE_BATCH_ROWS,
    ) -> int:
        """按批执行 DELETE/UPDATE，每批独立短事务；返回累计受影响行数。"""
        total = 0
        while True:
            with connect() as conn:
                cursor = conn.execute(statement, (*params, batch_size))
                affected = max(int(cursor.rowcount or 0), 0)
            if affected <= 0:
                break
            total += affected
            if affected < batch_size:
                break
        return total

    def _delete_expired_imports(self, table: str) -> int:
        return self._batched_statement(
            self._store.connect,
            f"""
            DELETE FROM {table} WHERE rowid IN (
              SELECT rowid FROM {table}
              WHERE datetime(expires_at) < datetime(?) LIMIT ?
            )
            """,
            ((utc_now().strftime("%Y-%m-%d %H:%M:%S")),),
        )

    def _count_workflow_run_cascades(self, history_cutoff: str) -> dict[str, int]:
        """统计将随 workflow_runs 级联删除的从属行数（外键 ON DELETE CASCADE）。"""
        stale_where = (
            "FROM workflow_runs WHERE datetime(COALESCE(finished_at, started_at)) < datetime(?)"
        )
        with self._store.connect() as conn:
            node_runs = conn.execute(
                f"""
                SELECT COUNT(*) FROM workflow_node_runs
                WHERE run_id IN (SELECT run_id {stale_where})
                """,
                (history_cutoff,),
            ).fetchone()[0]
            run_artifacts = conn.execute(
                f"""
                SELECT COUNT(*) FROM workflow_run_artifacts
                WHERE run_id IN (SELECT run_id {stale_where})
                """,
                (history_cutoff,),
            ).fetchone()[0]
        return {
            "workflow_node_runs": int(node_runs),
            "workflow_run_artifacts": int(run_artifacts),
        }

    # -- checkpoint / VACUUM / marker --

    def _checkpoint_databases(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for label, path in (
            ("main", self._store.db_path),
            ("logs", self._store.log_db_path),
        ):
            if label == "logs" and path == self._store.db_path:
                continue
            conn = sqlite3.connect(path, timeout=30)
            try:
                row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                result[label] = list(row) if row else None
            except sqlite3.Error as exc:
                result[label] = f"error: {exc}"
                logger.warning("wal_checkpoint 失败 db=%s 原因=%s", path, exc)
            finally:
                conn.close()
        return result

    def _vacuum_database(self, db_path: Path, *, label: str) -> float:
        started = time.monotonic()
        before = _db_disk_size(db_path)
        conn = sqlite3.connect(db_path, timeout=30)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("VACUUM")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
        duration = time.monotonic() - started
        after = _db_disk_size(db_path)
        logger.info(
            "数据库 VACUUM 完成 db=%s 耗时=%.1fs 大小=%s→%s",
            label,
            duration,
            _format_size(before),
            _format_size(after),
        )
        return duration

    def _vacuum_ledger_database(self) -> str:
        """台账库仅在 freelist 明显偏高时 VACUUM，否则跳过（数据不受 TTL 影响）。"""
        path = self._ledger_db_path
        if path is None or not path.exists():
            return "skipped"
        conn = sqlite3.connect(path, timeout=30)
        try:
            freelist = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
            page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
        finally:
            conn.close()
        threshold = max(
            LEDGER_VACUUM_MIN_FREELIST_PAGES,
            int(page_count * LEDGER_VACUUM_FREELIST_RATIO),
        )
        if freelist <= threshold:
            logger.info(
                "台账库 freelist 不高，跳过 VACUUM freelist=%d pages=%d 阈值=%d",
                freelist,
                page_count,
                threshold,
            )
            return "skipped"
        self._vacuum_database(path, label="台账库 agent-bridge-ledgers.db")
        return "done"

    def _marker(self, key: str) -> str | None:
        with self._store.connect() as conn:
            ensure_data_retention_meta(conn)
            row = conn.execute(
                "SELECT value FROM data_retention_meta WHERE key = ?", (key,)
            ).fetchone()
        return str(row["value"]) if row else None

    def _set_marker(self, key: str, value: str) -> None:
        with self._store.connect() as conn:
            ensure_data_retention_meta(conn)
            conn.execute(
                """
                INSERT INTO data_retention_meta (key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (key, value),
            )

    def _migrate_legacy_log_retention(self) -> None:
        """把旧 ``log_retention_days`` 的显式设置迁移到新的历史窗口。

        与旧默认 180 相同的值视为未显式配置，直接采用新默认 20/60；其余值
        说明管理员表达过保留意图，映射为 ``history_days = 旧值``、
        ``detail_days = min(20, 旧值)``，只迁移一次（marker 守卫）。
        """
        LEGACY_DEFAULT = 180  # noqa: N806
        with self._store.connect() as conn:
            row = conn.execute(
                "SELECT log_retention_days FROM knowledge_sync_config WHERE id = 1"
            ).fetchone()
        if row is None:
            return
        legacy = int(row["log_retention_days"] or LEGACY_DEFAULT)
        if legacy == LEGACY_DEFAULT:
            return
        detail = min(DEFAULT_DETAIL_DAYS, legacy)
        logger.info(
            "旧运行日志保留期迁移为生命周期配置 log_retention_days=%s → detail=%s history=%s",
            legacy,
            detail,
            legacy,
        )
        with self._store.connect() as conn:
            conn.execute(
                """
                UPDATE knowledge_sync_config
                SET retention_detail_days = ?, retention_history_days = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = 1
                """,
                (detail, legacy),
            )


class DataRetentionScheduler:
    """每日 ``cleanup_time`` 触发一次生命周期清理的 APScheduler 调度器。"""

    def __init__(self, service: DataRetentionService, store) -> None:
        self._service = service
        self._store = store
        self._scheduler: Any = None
        self._cleanup_time: str | None = None

    def start(self) -> None:
        if self._scheduler is not None and self._scheduler.running:
            return
        self._refresh_jobs()
        if self._scheduler is None:
            return
        self._scheduler.start()
        logger.info("数据生命周期调度器已启动 cleanup_time=%s", self._cleanup_time)

    def stop(self) -> None:
        scheduler = self._scheduler
        self._scheduler = None
        if scheduler is not None and scheduler.running:
            scheduler.shutdown(wait=False)
        logger.info("数据生命周期调度器已停止")

    def refresh(self) -> None:
        """配置变更后重建触发器；调度器未启动时只更新待用配置。"""
        if self._scheduler is None or not self._scheduler.running:
            self._cleanup_time = None
            return
        self._refresh_jobs()

    def get_status(self) -> dict[str, Any]:
        return {
            "running": bool(self._scheduler is not None and self._scheduler.running),
            "cleanup_time": self._cleanup_time or DEFAULT_CLEANUP_TIME,
        }

    def _refresh_jobs(self) -> None:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger

        cleanup_time = self._resolve_cleanup_time()
        if cleanup_time is None:
            logger.warning("数据生命周期清理时间配置不合法，调度器不启动")
            return
        hour, minute = cleanup_time.split(":", 1)
        if self._scheduler is None:
            self._scheduler = BackgroundScheduler(
                timezone="UTC", job_defaults={"coalesce": True, "max_instances": 1}
            )
        if self._cleanup_time == cleanup_time and self._scheduler.get_job("data-retention-cleanup"):
            return
        self._scheduler.remove_all_jobs()
        self._scheduler.add_job(
            self._run_cleanup,
            CronTrigger(hour=int(hour), minute=int(minute), timezone="UTC"),
            id="data-retention-cleanup",
            name="数据生命周期清理",
        )
        self._cleanup_time = cleanup_time

    def _resolve_cleanup_time(self) -> str | None:
        try:
            return self._service.retention_config()["cleanup_time"]
        except Exception:
            logger.warning("数据生命周期配置解析失败，回落默认清理时间", exc_info=True)
            return DEFAULT_CLEANUP_TIME

    def _run_cleanup(self) -> None:
        try:
            self._service.run_daily_cleanup()
        except Exception:
            logger.error("数据生命周期定时清理失败", exc_info=True)
