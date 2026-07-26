"""与 Profile 绑定、但不由 ``profile use`` 自动安装的 Hook 命令。"""

from __future__ import annotations

import getpass
import json
import logging
import re
import sys
from typing import Annotated, Any

import typer

from agent_bridge.client import AgentBridgeClient
from agent_bridge.core.config import default_user


logger = logging.getLogger(__name__)

profile_hook_app = typer.Typer(
    help="手工调用的 Profile Hook",
    no_args_is_help=True,
)
claude_code_hook_app = typer.Typer(
    help="Claude Code Profile Hook",
    no_args_is_help=True,
)
profile_hook_app.add_typer(claude_code_hook_app, name="claude-code")

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


@claude_code_hook_app.command("retrieval-probe")
def retrieval_probe_hook(
    profile: Annotated[
        str,
        typer.Option("--profile", help="Agent Bridge profile key"),
    ],
    server_url: Annotated[
        str,
        typer.Option("--server-url", help="Agent Bridge API base URL"),
    ] = "http://127.0.0.1:8765",
    timeout: Annotated[
        int,
        typer.Option("--timeout", min=1, max=30, help="探测超时秒数"),
    ] = 12,
) -> None:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            return
        if payload.get("hook_event_name") != "UserPromptSubmit":
            return
        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            return
        client = AgentBridgeClient(server_url, default_user(getpass.getuser()))
        response = client.probe_retrieval(
            {
                "profile_key": profile,
                "prompt": prompt,
                "session_id": str(payload.get("session_id") or ""),
                "keyword_limit": 8,
                "result_limit": 3,
                "timeout_seconds": timeout,
            },
            timeout=float(timeout + 2),
        )
    except Exception as exc:
        logger.warning(
            "Claude Code 检索探测 Hook 调用失败 profile=%s 原因=%s",
            profile,
            exc,
        )
        return

    reminder = render_probe_reminder(response)
    if not reminder:
        return
    typer.echo(reminder, err=True)
    raise typer.Exit(2)


def render_probe_reminder(
    payload: dict[str, Any],
    *,
    max_chars: int = 8000,
) -> str:
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
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
            lines.append(
                f"- {_target_label(target)}：{qualifier} {count} 条"
            )
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
    targets: list[dict[str, Any]],
    keyword: str,
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
    resource_name = _safe_line(
        target.get("resource_name") or target.get("resource_key") or ""
    )
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
