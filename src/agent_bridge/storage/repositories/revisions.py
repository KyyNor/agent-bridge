"""资产版本快照的公共读写逻辑。

``script_revisions``、``skill_prompt_revisions`` 和
``workflow_definition_revisions`` 三张表结构高度一致，CRUD 模式相同。
本模块抽取共用 SQL，各 repository 只需提供表名、键列名等元数据即可复用，
避免三份近乎逐行重复的实现。各调用方仍保留自己的方法名与参数名，对外
API 不变。

调用方负责在自己的事务连接内调用这些函数，因此这里不开连接、不提交。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from agent_bridge.storage.types import row_to_dict


def _entity_key_column(key_column: str) -> str:
    return f"{key_column} AS entity_key"


def list_revisions(
    conn: sqlite3.Connection,
    *,
    table: str,
    key_column: str,
    key_value: str,
    limit: int = 100,
    extra_columns: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """按 key 倒序返回版本列表（不含 snapshot 正文）。"""
    bounded = min(max(limit, 1), 500)
    columns = ", ".join(
        [_entity_key_column(key_column), "revision_no", "content_hash", "created_by", *extra_columns, "created_at"]
    )
    rows = conn.execute(
        f"""
        SELECT {columns}
        FROM {table}
        WHERE {key_column} = ?
        ORDER BY revision_no DESC
        LIMIT ?
        """,
        (key_value, bounded),
    ).fetchall()
    return [dict(row) for row in rows]


def get_revision(
    conn: sqlite3.Connection,
    *,
    table: str,
    key_column: str,
    key_value: str,
    revision_no: int,
    snapshot_label: str,
    extra_columns: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    """取单个版本并解析 snapshot 正文；不存在返回 ``None``。"""
    columns = ", ".join(
        [_entity_key_column(key_column), "revision_no", "content_hash", "snapshot_json", "created_by", *extra_columns, "created_at"]
    )
    item = row_to_dict(
        conn.execute(
            f"""
            SELECT {columns}
            FROM {table}
            WHERE {key_column} = ? AND revision_no = ?
            """,
            (key_value, revision_no),
        ).fetchone()
    )
    if item is None:
        return None
    snapshot_json = item.pop("snapshot_json", None)
    try:
        snapshot = json.loads(snapshot_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"corrupt {snapshot_label} revision snapshot") from exc
    if not isinstance(snapshot, dict):
        raise ValueError(f"corrupt {snapshot_label} revision snapshot")
    item["snapshot"] = snapshot
    return item


def create_revision(
    conn: sqlite3.Connection,
    *,
    table: str,
    key_column: str,
    key_value: str,
    content_hash: str,
    snapshot: dict[str, Any],
    actor: str,
    owner_table: str,
    snapshot_label: str,
    extra_columns: tuple[str, ...] = (),
    extra_values: tuple[Any, ...] = (),
) -> dict[str, Any]:
    """分配下一个 revision_no、写入快照、回写主表 current_revision_no。

    返回新建版本的列表视图（不含 snapshot 正文，与 :func:`list_revisions`
    字段对齐）。``extra_columns``/``extra_values`` 用于 workflow 的 ``source``
    这类额外列；二者长度必须一致。
    """
    if len(extra_columns) != len(extra_values):
        raise ValueError("extra_columns 与 extra_values 长度不一致")
    snapshot_json = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
    row = conn.execute(
        f"SELECT COALESCE(MAX(revision_no), 0) FROM {table} WHERE {key_column} = ?",
        (key_value,),
    ).fetchone()
    next_no = int(row[0]) + 1
    insert_columns = ", ".join([key_column, "revision_no", "content_hash", "snapshot_json", "created_by", *extra_columns])
    placeholders = ", ".join(["?"] * (5 + len(extra_columns)))
    conn.execute(
        f"INSERT INTO {table} ({insert_columns}) VALUES ({placeholders})",
        (key_value, next_no, content_hash, snapshot_json, actor, *extra_values),
    )
    conn.execute(
        f"UPDATE {owner_table} SET current_revision_no = ? WHERE {key_column} = ?",
        (next_no, key_value),
    )
    return list_revisions(
        conn,
        table=table,
        key_column=key_column,
        key_value=key_value,
        limit=1,
        extra_columns=extra_columns,
    )[0]
