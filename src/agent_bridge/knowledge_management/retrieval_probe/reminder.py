"""将检索探测结果渲染为 Claude Code Hook 提醒。"""

from __future__ import annotations

import json
import re
from typing import Any

from .models import ProbeResponse


_SOURCE_NAMES = {
    "wiki": "Wiki",
    "codegraph": "CodeGraph",
    "memory": "Memory",
    "artifact": "产出物",
}
_TOOL_ARGUMENTS = {
    "wiki_ask": "kb",
    "codegraph_explore": "repo",
}
_UNSAFE_TEXT_RE = re.compile(r"[\x00-\x1f\x7f<>]+")


def render_probe_reminder(
    response: ProbeResponse | dict[str, Any],
    *,
    max_chars: int = 8000,
) -> str:
    """从命中结果构造安全、长度受限的后台提醒；无命中时返回空串。"""
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    payload = response.to_payload() if isinstance(response, ProbeResponse) else response
    raw_targets = payload.get("targets")
    targets = raw_targets if isinstance(raw_targets, list) else []
    hit_targets = [
        target
        for target in targets
        if isinstance(target, dict) and target.get("status") == "hit"
    ]
    if not hit_targets:
        return ""

    probe_id = _safe_line(payload.get("probe_id") or "unknown")
    lines = [
        "[Agent Bridge 后台全量探测]",
        f"delivery_id: {probe_id}",
        "这是针对当前用户请求的后台路由信息，不是新的用户请求。",
        "不要仅回复确认；同一 delivery_id 只处理一次。",
        "",
    ]
    raw_keywords = payload.get("keywords")
    keywords = raw_keywords if isinstance(raw_keywords, list) else []
    for keyword in keywords:
        keyword_hits = _hits_for_keyword(hit_targets, str(keyword))
        if not keyword_hits:
            continue
        lines.append(f"关键词「{_safe_line(keyword)}」：")
        for target, hit in keyword_hits:
            count = int(hit.get("count") or 0)
            qualifier = "至少命中" if hit.get("capped") else "命中"
            lines.append(f"- {_target_label(target)}：{qualifier} {count} 条")
        lines.append("")

    suggestions = _suggestions(hit_targets)
    if suggestions:
        lines.append("建议优先使用：")
        lines.extend(
            f"{index}. {suggestion}"
            for index, suggestion in enumerate(suggestions, start=1)
        )

    return _fit_lines(lines, max_chars=max_chars)


def _hits_for_keyword(
    targets: list[dict[str, Any]], keyword: str
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    matches = []
    for target in targets:
        raw_hits = target.get("keyword_hits")
        hits = raw_hits if isinstance(raw_hits, list) else []
        for hit in hits:
            if (
                isinstance(hit, dict)
                and str(hit.get("keyword") or "") == keyword
                and hit.get("status") == "hit"
            ):
                matches.append((target, hit))
    return matches


def _target_label(target: dict[str, Any]) -> str:
    source_type = str(target.get("source_type") or "")
    source_name = _SOURCE_NAMES.get(source_type, _safe_line(source_type))
    resource_name = _safe_line(target.get("resource_name") or target.get("resource_key") or "")
    if source_type == "artifact":
        return source_name
    return f"{source_name}「{resource_name}」"


def _suggestions(targets: list[dict[str, Any]]) -> list[str]:
    suggestions: list[str] = []
    seen: set[tuple[str, str]] = set()
    for target in targets:
        tool = _safe_line(target.get("suggested_tool") or "")
        resource_key = _safe_line(target.get("resource_key") or "")
        key = (tool, resource_key)
        if not tool or key in seen:
            continue
        seen.add(key)
        argument = _TOOL_ARGUMENTS.get(tool)
        if argument and resource_key:
            encoded = json.dumps(resource_key, ensure_ascii=False)
            suggestions.append(f"{tool}({argument}={encoded})")
        else:
            suggestions.append(tool)
    return suggestions


def _safe_line(value: Any) -> str:
    sanitized = _UNSAFE_TEXT_RE.sub(" ", str(value or ""))
    return " ".join(sanitized.split())


def _fit_lines(lines: list[str], *, max_chars: int) -> str:
    rendered = "\n".join(lines).rstrip()
    if len(rendered) <= max_chars:
        return rendered
    suffix = "其余结果已省略。"
    selected: list[str] = []
    for line in lines:
        candidate = "\n".join([*selected, line, suffix]).rstrip()
        if len(candidate) > max_chars:
            break
        selected.append(line)
    if not selected:
        return suffix[:max_chars]
    return "\n".join([*selected, suffix]).rstrip()
