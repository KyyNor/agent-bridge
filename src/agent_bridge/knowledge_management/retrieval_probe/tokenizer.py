"""将用户问题拆成适合独立探测的多个关键词。"""

from __future__ import annotations

import logging
import re

import jieba


jieba.setLogLevel(logging.WARNING)

_SEGMENT_RE = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+|[A-Za-z0-9_./:-]+"
)
_STOPWORDS = {
    "一下",
    "之前",
    "什么",
    "最终",
    "检查",
    "怎么",
    "如何",
    "我们",
    "是否",
    "用户",
    "请",
    "帮我",
    "帮忙",
    "一下子",
    "的",
    "了",
    "和",
    "与",
    "是",
    "在",
    "有",
}


def extract_probe_keywords(text: str, limit: int = 8) -> list[str]:
    """按原始顺序返回去重后的中文词和 ASCII 标识符。"""
    if limit < 1:
        raise ValueError("limit must be positive")
    tokens: list[str] = []
    for segment in _SEGMENT_RE.findall(text or ""):
        if segment[0] >= "\u3400":
            tokens.extend(token.strip() for token in jieba.lcut(segment))
        else:
            tokens.append(segment)

    result: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        normalized = token.strip()
        if not normalized or normalized in _STOPWORDS:
            continue
        if normalized[0] >= "\u3400" and len(normalized) < 2:
            continue
        if normalized[0] < "\u3400" and len(normalized) < 2:
            continue
        dedupe_key = normalized.casefold()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        result.append(normalized)
        if len(result) >= limit:
            break
    return result
