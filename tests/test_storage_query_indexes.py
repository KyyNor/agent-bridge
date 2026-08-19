"""任务队列与概览聚合查询的索引护栏。

workflow_runs 与 tool_call_logs 行内均内嵌大 JSON 字段，缺索引的关联或
聚合会退化为逐行回表；这里固定索引存在性与查询计划形状，防止回归。
"""

from __future__ import annotations

import sqlite3

# list_workflow_tasks 中按任务维度取最近完成 run 的标量子查询（去掉外层关联）。
_TASK_RUN_LOOKUP_SQL = """
SELECT workflow_content_hash FROM workflow_runs successful
WHERE successful.workflow_key = ?
  AND successful.task_key = ?
  AND successful.task_version = ?
  AND successful.status = 'completed'
ORDER BY successful.finished_at DESC, successful.id DESC
LIMIT 1
"""

# list_tool_call_logs_page 内的状态分组统计。
_LOG_STATUS_COUNT_SQL = """
SELECT
  COUNT(*) AS all_count,
  SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success,
  SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS error,
  SUM(CASE WHEN status = 'blocked' THEN 1 ELSE 0 END) AS blocked,
  SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running
FROM tool_call_logs
WHERE created_at >= ? AND created_at < ?
"""

# aggregate_tool_call_stats 的按天维度聚合（概览趋势图使用的形态）。
_LOG_STATS_BUCKET_SQL = """
SELECT
  strftime('%Y-%m-%d %H:00:00', tool_call_logs.created_at) AS bucket,
  tool_call_logs.resource_type,
  tool_call_logs.source_type,
  tool_call_logs.status,
  COUNT(*) AS calls,
  ROUND(AVG(COALESCE(tool_call_logs.duration_ms, 0)), 0) AS avg_duration_ms,
  MAX(tool_call_logs.duration_ms) AS max_duration_ms
FROM tool_call_logs
WHERE tool_call_logs.created_at >= ? AND tool_call_logs.created_at < ?
GROUP BY bucket, tool_call_logs.resource_type, tool_call_logs.source_type, tool_call_logs.status
ORDER BY calls DESC
"""


def _index_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = ?",
            (table,),
        )
    }


def _plan_details(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[str]:
    return [str(row[3]) for row in conn.execute("EXPLAIN QUERY PLAN " + sql, params)]


def test_workflow_runs_task_index_created_on_init(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    with store.connect() as conn:
        assert "idx_workflow_runs_task" in _index_names(conn, "workflow_runs")


def test_task_run_lookup_uses_task_index_without_temp_sort(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    with store.connect() as conn:
        details = _plan_details(conn, _TASK_RUN_LOOKUP_SQL, ("w", "page:alpha", "v1"))
    assert any("idx_workflow_runs_task" in detail for detail in details), details
    assert not any("TEMP B-TREE" in detail for detail in details), details


def test_tool_call_logs_stats_index_created_in_split_log_db(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path, wm_paths.log_db_path)
    store.init_schema()
    with store.log_connect() as conn:
        assert "idx_tool_call_logs_stats" in _index_names(conn, "tool_call_logs")


def test_tool_call_logs_stats_index_created_in_single_db(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    with store.connect() as conn:
        assert "idx_tool_call_logs_stats" in _index_names(conn, "tool_call_logs")


def test_dashboard_log_aggregates_use_covering_index(wm_paths):
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(wm_paths.db_path, wm_paths.log_db_path)
    store.init_schema()
    window = ("2026-08-01T00:00:00Z", "2026-08-19T00:00:00Z")
    with store.log_connect() as conn:
        for sql in (_LOG_STATUS_COUNT_SQL, _LOG_STATS_BUCKET_SQL):
            details = _plan_details(conn, sql, window)
            assert any(
                "USING COVERING INDEX idx_tool_call_logs_stats" in detail
                for detail in details
            ), details
