"""HTML report generation for summary workflows.

Builds the reporter prompt (mirroring the design-agent pattern: instruct the
agent to ``load_skill`` while also inlining the skill text as a fallback) and
defines the structured output schema the reporter must conform to.
"""
from __future__ import annotations

from typing import Any

# Output schema for the workflow_html_reporter agent. The runner enforces that
# ``html`` looks like a real HTML document before persisting it.
HTML_REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "summary", "html"],
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "html": {"type": "string"},
        "source_artifact_ids": {"type": "array", "items": {"type": "string"}},
    },
}

# Hard limit on the generated HTML body to keep storage and the frontend sane.
HTML_MAX_BYTES = 5 * 1024 * 1024


def build_report_prompt(
    *,
    skill_name: str,
    skill_prompt: str,
    workflow: dict[str, Any],
    run: dict[str, Any],
    markdown_artifacts: list[dict[str, Any]],
    run_logs: list[dict[str, Any]],
    agent_events_summary: str,
) -> str:
    """Assemble the prompt for the workflow_html_reporter agent.

    Mirrors ``_design_prompt`` (api/routes/agent_runs.py): instruct the agent to
    call ``execute('built-in', 'load_skill')`` first, but also inline the skill
    text so the constraint is visible even if the MCP call is unavailable.
    """
    md_listing = "\n".join(
        f"- {item.get('title') or item.get('path') or 'artifact'} "
        f"(path={item.get('path')}; summary={item.get('summary') or ''})"
        for item in markdown_artifacts
    ) or "（无）"

    md_bodies = "\n\n".join(
        f"### {item.get('title') or item.get('path')}\n路径：{item.get('path')}\n摘要：{item.get('summary') or ''}\n\n{item.get('content') or ''}"
        for item in markdown_artifacts
    ) or "（本轮无 Markdown 产物正文）"

    log_lines = "\n".join(
        f"[{log.get('level', 'info')}] {log.get('stage', '')}: {log.get('message', '')}"
        for log in run_logs
    ) or "（本轮无运行日志）"

    return "\n\n".join(
        [
            "你是 Agent Bridge 的 html_report 设计 agent。请把本次 workflow run 的总结产物转写成一份面向人类阅读的完整 HTML 报告。",
            (
                f"必须先遵循内置技能 {skill_name}。如果你需要工具，请优先执行 "
                f"execute service='built-in' tool_name='load_skill' params={{\"skill_name\":\"{skill_name}\"}}；"
                "下方也内联提供了当前技能内容作为约束。"
            ),
            (
                "本轮 Markdown 产物是核心输入，请以它们为主进行总结：\n" + md_listing
            ),
            (
                "你还可以参考以下辅助上下文，但不要把中间日志或原始 transcript 原样长篇堆入页面，应摘要化或折叠展示："
            ),
            f"运行日志摘要：\n{log_lines}",
            f"子 agent / 事件摘要：\n{agent_events_summary or '（无）'}",
            "输出约束：返回完整 HTML 文档，内联 CSS，无外链脚本，无图片（第一版）。",
            f"工作流：{workflow.get('name') or workflow.get('workflow_key')}（{workflow.get('description') or ''}）",
            (
                f"运行：run_id={run.get('run_id')}，task_key={run.get('task_key') or ''}"
            ),
            f"{skill_name} 内容：\n{skill_prompt}",
            "本轮 Markdown 产物正文：\n" + md_bodies,
        ]
    )


def summarize_agent_events(agent_runs: list[dict[str, Any]]) -> str:
    """Render a compact, human-readable summary of the agent runs associated
    with a workflow run. Only surfaces lightweight fields (agent name, status,
    turn count, error) — never full transcripts, which can be huge."""
    if not agent_runs:
        return ""
    lines: list[str] = []
    for item in agent_runs:
        name = item.get("agent_name") or "agent"
        status = item.get("status") or ""
        turns = item.get("num_turns")
        ok = item.get("ok")
        error = item.get("error")
        bits = [f"agent={name}"]
        if status:
            bits.append(f"status={status}")
        if turns is not None:
            bits.append(f"turns={turns}")
        if ok is False and error:
            bits.append(f"error={error}")
        lines.append(" | ".join(bits))
    return "\n".join(lines)


def looks_like_html(text: str) -> bool:
    """Cheap sanity check that the reporter returned an HTML document."""
    lowered = (text or "").lower()
    return "<html" in lowered or "<body" in lowered
