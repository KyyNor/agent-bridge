"""工作流产物的中文分词与 FTS5 查询文本构造。"""

from __future__ import annotations

import logging
import re
import sqlite3
from typing import Any

import jieba


jieba.setLogLevel(logging.WARNING)


_SEARCH_SEGMENT_RE = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+|[A-Za-z0-9_./:-]+"
)
_ASCII_PREFIX_RE = re.compile(r"^[A-Za-z0-9_]+$")
_MIN_PREFIX_LENGTH = 3


def _segments(text: str) -> list[str]:
    """保留中文语段和 ASCII 标识符，丢弃 Markdown/标点分隔符。"""
    return _SEARCH_SEGMENT_RE.findall(text)


def tokenize_artifact_text(text: str) -> str:
    """将原文转成写入 FTS5 的空格分隔 token 文本。

    中文使用搜索引擎模式以提高短词召回；ASCII 连续片段保留原始字符，最终
    由 FTS5 的 ``unicode61`` tokenizer 按配置拆分，下划线仍保留在 token 内。
    """
    tokens: list[str] = []
    for segment in _segments(text):
        if segment[0] >= "\u3400":
            tokens.extend(str(token) for token in jieba.lcut_for_search(segment) if token.strip())
        else:
            tokens.append(segment)
    return " ".join(tokens)


def _query_tokens(query: str) -> list[str]:
    """使用精确模式拆分查询，避免搜索模式产生的长词重叠 token 阻断 AND。"""
    tokens: list[str] = []
    for segment in _segments(query):
        if segment[0] >= "\u3400":
            tokens.extend(str(token) for token in jieba.lcut(segment) if token.strip())
        else:
            tokens.append(segment)
    return list(dict.fromkeys(tokens))


def build_artifact_fts_query(query: str | None) -> str | None:
    """将普通查询转换为由多个安全 token 组成的 FTS5 AND 表达式。

    足够长的 ASCII 标识符使用前缀匹配，便于用 ``repo`` 检索
    ``repos/...``；短 token 和带分隔符的路径/标识符保持字面匹配，避免
    前缀扩展造成过宽召回。
    """
    if not query or not query.strip():
        return None
    tokens = _query_tokens(query)
    if not tokens:
        return None
    quoted = (_fts_query_token(token) for token in tokens)
    return " AND ".join(quoted)


def _fts_query_token(token: str) -> str:
    escaped = token.replace('"', '""')
    if len(token) >= _MIN_PREFIX_LENGTH and _ASCII_PREFIX_RE.fullmatch(token):
        return f'"{escaped}"*'
    return f'"{escaped}"'


def search_content_values(*, title: str, summary: str, path: str, content: str) -> tuple[str, str, str, str]:
    """返回 ``workflow_artifacts_search_content`` 的四个分词字段。"""
    return (
        tokenize_artifact_text(title),
        tokenize_artifact_text(summary),
        tokenize_artifact_text(path),
        tokenize_artifact_text(content),
    )


def upsert_workflow_artifact_search_content(conn: sqlite3.Connection, artifact: dict[str, Any]) -> None:
    """同步一个产物的分词副本，原始产物表仍是唯一事实来源。"""
    title, summary, path, content = search_content_values(
        title=str(artifact.get("title") or ""),
        summary=str(artifact.get("summary") or ""),
        path=str(artifact.get("path") or ""),
        content=str(artifact.get("content") or ""),
    )
    conn.execute(
        """
        INSERT INTO workflow_artifacts_search_content(id, title, summary, path, content)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          title = excluded.title,
          summary = excluded.summary,
          path = excluded.path,
          content = excluded.content
        """,
        (int(artifact["id"]), title, summary, path, content),
    )
